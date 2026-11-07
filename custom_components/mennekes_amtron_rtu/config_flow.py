"""Config flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from ._flow_connect import async_test_connection
from ._flow_gateway import GatewayChange
from ._flow_search import (
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    SearchResult,
    async_search,
    search_space,
)
from ._flow_serial import list_by_id_ports, list_comports, merge_port_options
from .config_schemas import (
    network_adapter_schema,
    network_auth_schema,
    network_schema,
    service_schema,
    user_schema,
)
from .data import DeviceIdentity
from .device.client import SerialConfig
from .device.client_errors import AmtronBusError, AmtronConnectionError
from .device.connection import (
    ConnectionConfig,
    NetworkConfig,
    validate_tcp_auth_password,
)
from .device.const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONFIGURE_GATEWAY,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_FRAME,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_SEARCH,
    CONF_SERIAL_NUMBER,
    CONF_STOPBITS,
    CONF_TCP_AUTH_PASSWORD,
    CONF_TCP_AUTHENTICATION,
    CONF_TCP_PORT,
    CONNECTION_RTU_OVER_TCP,
    CONNECTION_SERIAL,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_FRAME,
    DEFAULT_NAME,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
    DOMAIN,
    SEARCH_FULL,
    SEARCH_OFF,
    SUPPORTED_FRAMES,
    frame_display,
    frame_label,
    frame_parts,
)
from .device.identity import async_read_identity, display_identifier
from .device.registers import VALIDATED_LAYOUT, layout_label
from .device.service_config import validate_token
from .device.service_discovery import APP_HOSTNAME
from .device.waveshare_auth import WaveshareAuthenticationRejectedError
from .device.waveshare_gateway import (
    GATEWAY_BAUDRATES,
    GatewayError,
    GatewayRollbackError,
    GatewaySettings,
    GatewaySnapshot,
    inspect_gateway,
)
from .entry_types import MennekesAmtronConfigEntry
from .options_flow import MennekesAmtronOptionsFlow
from .remote import CONF_SERVER_TOKEN, CONF_SERVER_URL, RemoteClient, server_url
from .remote_errors import ServiceConnectionError
from .supervisor_discovery import async_app_discoveries

_LOGGER = logging.getLogger(__name__)

RECONFIGURE_STEPS = ("reconfigure", "serial_reconfigure", "network_reconfigure")


def _discovered_connection(info: HassioServiceInfo) -> dict[str, Any]:
    """Validate every announcement, including one refreshed during a retry."""
    hostname = info.slug.replace("_", "-")
    if (
        not APP_HOSTNAME.fullmatch(hostname)
        or info.config.get("host") != hostname
        or type(info.config.get("port")) is not int
        or info.config["port"] != 8765
    ):
        raise ValueError("invalid app discovery")
    return {
        CONF_SERVER_URL: f"http://{hostname}:8765",
        CONF_SERVER_TOKEN: validate_token(info.config.get("token")),
    }


class MennekesAmtronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect one wallbox directly or through the independent service."""

    VERSION = 1

    def __init__(self) -> None:
        self._search_task: Any = None
        self._search_result: SearchResult | None = None
        self._search_error = "not_found"
        self._pending_input: dict[str, Any] | None = None
        self._pending_step: str = "serial"
        self._discovered_service: dict[str, Any] | None = None
        self._discovered_serial: str | None = None
        self._gateway_snapshot: GatewaySnapshot | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the service, a serial adapter or a network gateway."""
        return self.async_show_menu(
            step_id="user", menu_options=["server", "serial", "network"]
        )

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_serial_step("serial", user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Replace failed service credentials without recreating the device."""
        entry = self._get_reauth_entry()
        if CONF_SERVER_URL not in entry.data:
            if entry.data.get(CONF_TCP_AUTHENTICATION) is True:
                return await self.async_step_network_reauth()
            return self.async_abort(reason="reauth_not_supported")
        parts = urlsplit(str(entry.data[CONF_SERVER_URL]))
        if (
            parts.scheme == "http"
            and parts.port == 8765
            and APP_HOSTNAME.fullmatch(parts.hostname or "")
        ):
            return await self.async_step_reauth_app()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask only for the new token of a separately hosted service."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token = validate_token(user_input.get(CONF_SERVER_TOKEN))
                return await self._async_reauthenticate(token)
            except ServiceConnectionError as err:
                errors["base"] = err.reason
            except ValueError:
                errors["base"] = "service_auth_failed"
            except AmtronBusError:
                errors["base"] = "cannot_connect_service"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=service_schema(credentials_only=True),
            errors=errors,
        )

    async def async_step_network_reauth(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Replace only the stored login after confirming this wallbox's identity."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_reauthenticate_network(user_input.get(CONF_TCP_AUTH_PASSWORD))
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_TCP_AUTH_PASSWORD: user_input[CONF_TCP_AUTH_PASSWORD]},
                )
            except ValueError:
                errors[CONF_TCP_AUTH_PASSWORD] = "invalid_tcp_auth_password"
            except WaveshareAuthenticationRejectedError:
                errors["base"] = "gateway_authentication_failed"
            except AmtronConnectionError:
                errors["base"] = "cannot_connect_network"
            except AmtronBusError:
                errors["base"] = "invalid_network_response"
        return self.async_show_form(
            step_id="network_reauth", data_schema=network_auth_schema(), errors=errors,
        )

    async def _async_reauthenticate_network(self, password: Any) -> None:
        entry = self._get_reauth_entry()
        original_data = dict(entry.data)
        config = _network_config({**original_data, CONF_TCP_AUTH_PASSWORD: password})
        loaded = entry.state is ConfigEntryState.LOADED
        if loaded and not await self.hass.config_entries.async_unload(entry.entry_id):
            raise AmtronConnectionError("cannot release the active connection")
        try:
            identity = await async_test_connection(config)
            await self.async_set_unique_id(identity.serial_number or f"{DOMAIN}_{config.port}_{config.device_id}")
            self._abort_if_unique_id_mismatch()
            if dict(entry.data) != original_data:
                raise AmtronConnectionError("connection settings changed during authentication")
        except BaseException:
            if loaded:
                await self.hass.config_entries.async_setup(entry.entry_id)
            raise

    async def async_step_reauth_app(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retrieve credentials only from the same trusted Supervisor app."""
        entry = self._get_reauth_entry()
        errors = {"base": "service_discovery_unavailable"}
        matching: list[dict[str, Any]] = []
        for info in await async_app_discoveries(self.hass):
            try:
                data = _discovered_connection(info)
            except ValueError:
                continue
            if data[CONF_SERVER_URL] == entry.data[CONF_SERVER_URL]:
                matching.append(data)
        if len(matching) == 1:
            try:
                return await self._async_reauthenticate(matching[0][CONF_SERVER_TOKEN])
            except ServiceConnectionError as err:
                errors["base"] = err.reason
            except AmtronBusError:
                errors["base"] = "cannot_connect_service"
        return self.async_show_form(step_id="reauth_app", errors=errors)

    async def _async_reauthenticate(self, token: str) -> ConfigFlowResult:
        """Read identity at the existing endpoint before persisting a token."""
        entry = self._get_reauth_entry()
        url = str(entry.data[CONF_SERVER_URL])
        client = RemoteClient(async_get_clientsession(self.hass), url, token)
        try:
            await client.async_connect()
            unique_id = client.identity.serial_number or f"{DOMAIN}_{url}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_mismatch()
        finally:
            await client.async_close()
        updates = {CONF_SERVER_TOKEN: token}
        if entry.update_listeners:
            changed = entry.data.get(CONF_SERVER_TOKEN) != token
            result = self.async_update_and_abort(entry, data_updates=updates)
            if not changed:
                # A recovered endpoint can accept the original token again.
                # No data change means the update listener will not reload it.
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return result
        return self.async_update_reload_and_abort(entry, data_updates=updates)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["server", "serial_reconfigure", "network_reconfigure"],
        )

    async def async_step_serial_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_serial_step("serial_reconfigure", user_input)

    async def async_step_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_network_step("network", user_input)

    async def async_step_network_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_network_step("network_reconfigure", user_input)

    async def _async_network_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                if user_input.get(CONF_CONFIGURE_GATEWAY) is True:
                    base = _network_config({**user_input, CONF_TCP_AUTHENTICATION: False, CONF_TCP_AUTH_PASSWORD: ""})
                    snapshot = await inspect_gateway(base.host)
                    if not snapshot.can_configure:
                        errors["base"] = "gateway_unsupported"
                    else:
                        self._gateway_snapshot = snapshot
                        self._pending_input = dict(user_input)
                        self._pending_step = step_id
                        return await self.async_step_network_adapter()
                elif user_input.get(CONF_TCP_AUTHENTICATION) is True:
                    # Validate the endpoint before asking for its credentials.
                    _network_config({**user_input, CONF_TCP_AUTHENTICATION: False, CONF_TCP_AUTH_PASSWORD: ""})
                    self._pending_input = dict(user_input)
                    self._pending_step = step_id
                    return await self.async_step_network_auth()
                else:
                    config = _network_config(user_input)
                    return await self._async_test_and_finish(step_id, user_input, config)
            except GatewayError as err:
                errors["base"] = err.reason
            except ValueError:
                errors["base"] = "invalid_network_config"
            except AmtronConnectionError as err:
                _LOGGER.debug("Network connection test failed: %s", err)
                errors["base"] = "cannot_connect_network"
            except AmtronBusError as err:
                _LOGGER.debug("Network identity read failed: %s", err)
                errors["base"] = "invalid_network_response"
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                network_schema(), self._suggested_values(step_id, user_input)
            ),
            errors=errors,
            description_placeholders={
                "validated_layout": layout_label(VALIDATED_LAYOUT)
            },
        )

    async def async_step_network_auth(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Authenticate an already configured adapter without changing it."""
        pending = getattr(self, "_pending_input", None)
        if pending is None:
            return await self.async_step_network()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                config = _network_config({**pending, **user_input})
                return await self._async_test_and_finish(self._pending_step, pending, config)
            except ValueError:
                errors[CONF_TCP_AUTH_PASSWORD] = "invalid_tcp_auth_password"
            except WaveshareAuthenticationRejectedError:
                errors["base"] = "gateway_authentication_failed"
            except AmtronConnectionError:
                errors["base"] = "cannot_connect_network"
            except AmtronBusError:
                errors["base"] = "invalid_network_response"
        return self.async_show_form(
            step_id="network_auth", data_schema=network_auth_schema(), errors=errors,
        )

    async def async_step_network_adapter(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Preview the recognized adapter, then explicitly apply and verify it."""
        snapshot = getattr(self, "_gateway_snapshot", None)
        pending = getattr(self, "_pending_input", None)
        if snapshot is None or pending is None:
            return await self.async_step_network()
        errors: dict[str, str] = {}
        suggested = {
            CONF_BAUDRATE: snapshot.baudrate,
            CONF_FRAME: snapshot.frame,
            CONF_TCP_AUTHENTICATION: pending.get(CONF_TCP_AUTHENTICATION) is True or snapshot.tcp_authentication,
        }
        if self._pending_step in RECONFIGURE_STEPS:
            old_data = self._get_reconfigure_entry().data
            if old_data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL) == CONNECTION_SERIAL and CONF_PORT in old_data:
                serial = _serial_config(dict(old_data))
                suggested.update({
                    CONF_BAUDRATE: serial.baudrate,
                    CONF_FRAME: frame_label(serial.bytesize, serial.parity, serial.stopbits),
                })
        if suggested[CONF_BAUDRATE] not in GATEWAY_BAUDRATES:
            suggested[CONF_BAUDRATE] = DEFAULT_BAUDRATE
        if suggested[CONF_FRAME] not in SUPPORTED_FRAMES:
            suggested[CONF_FRAME] = DEFAULT_FRAME
        if user_input is not None:
            suggested.update({key: value for key, value in user_input.items() if key != CONF_TCP_AUTH_PASSWORD})
            try:
                password = user_input.get(CONF_TCP_AUTH_PASSWORD, "")
                authentication = user_input[CONF_TCP_AUTHENTICATION]
                if password or authentication:
                    validate_tcp_auth_password(password)
                config = _network_config({
                    **pending, CONF_TCP_AUTHENTICATION: authentication,
                    CONF_TCP_AUTH_PASSWORD: password if authentication else "",
                })
                change = GatewayChange(
                    config.host, snapshot,
                    GatewaySettings(
                        baudrate=user_input[CONF_BAUDRATE], frame=user_input[CONF_FRAME],
                        connection_type=config.connection_type,
                        tcp_port=config.tcp_port, tcp_authentication=authentication,
                    ),
                    password,
                )
                return await self._async_test_and_finish(self._pending_step, pending, config, gateway=change)
            except GatewayError as err:
                errors["base"] = err.reason
            except ValueError:
                errors["base"] = "invalid_gateway_config"
            except AmtronConnectionError:
                errors["base"] = "cannot_connect_network"
            except AmtronBusError:
                errors["base"] = "invalid_network_response"
        return self.async_show_form(
            step_id="network_adapter",
            data_schema=self.add_suggested_values_to_schema(network_adapter_schema(GATEWAY_BAUDRATES), suggested),
            errors=errors,
            description_placeholders={
                "model": snapshot.model, "firmware": snapshot.firmware,
                "baudrate": str(snapshot.baudrate), "frame": snapshot.frame,
                "tcp_port": str(snapshot.tcp_port),
            },
        )

    async def async_step_server(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Probe the service without sending any charging commands."""
        if user_input is None:
            apps = await async_app_discoveries(self.hass)
            if len(apps) == 1:
                return await self.async_step_hassio(apps[0])
            if len(apps) > 1:
                return self.async_abort(reason="multiple_service_apps")
        errors: dict[str, str] = {}
        reconfigure = self.context.get("source") == "reconfigure"
        entry = self._get_reconfigure_entry() if reconfigure else None
        if user_input is not None:
            try:
                url = server_url(str(user_input[CONF_SERVER_URL]))
                token = str(user_input[CONF_SERVER_TOKEN])
                if len(token) < 32:
                    raise ValueError("invalid token")
                client = RemoteClient(async_get_clientsession(self.hass), url, token)
                try:
                    await client.async_connect()
                    identity = client.identity
                    if identity.serial_number:
                        unique_id = identity.serial_number
                    elif entry is not None:
                        return self.async_abort(
                            reason="service_migration_requires_serial"
                        )
                    else:
                        unique_id = f"{DOMAIN}_{url}"
                    await self.async_set_unique_id(unique_id)
                    data = {CONF_SERVER_URL: url, CONF_SERVER_TOKEN: token}
                    if identity.serial_number:
                        data[CONF_SERIAL_NUMBER] = identity.serial_number
                    if entry is not None:
                        self._abort_if_unique_id_mismatch()
                        if not entry.update_listeners:
                            return self.async_update_reload_and_abort(entry, data=data)
                        return self.async_update_and_abort(entry, data=data)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=DEFAULT_NAME, data=data
                    )
                finally:
                    await client.async_close()
            except ServiceConnectionError as err:
                _LOGGER.warning("Modbus service setup: %s", err)
                errors["base"] = err.reason
            except ValueError, AmtronBusError:
                errors["base"] = "cannot_connect_service"
        defaults = user_input or (dict(entry.data) if entry is not None else {})
        return self.async_show_form(
            step_id="server",
            data_schema=self.add_suggested_values_to_schema(service_schema(), defaults),
            errors=errors,
        )

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Use Supervisor's authenticated app identity, never a supplied URL."""
        try:
            self._discovered_service = _discovered_connection(discovery_info)
        except ValueError:
            return self.async_abort(reason="invalid_service_discovery")
        hostname = discovery_info.slug.replace("_", "-")
        # A new app announcement may replace credentials in an unfinished
        # discovery. Keep one prompt and discard the stale private payload.
        for flow in self._async_in_progress():
            if (flow["context"].get("title_placeholders") or {}).get(
                "app"
            ) == discovery_info.slug:
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
        entry = await self.async_set_unique_id(f"{DOMAIN}_{hostname}")
        if entry is not None and entry.source == "ignore":
            return self.async_abort(reason="already_configured")
        self.context["title_placeholders"] = {
            "name": DEFAULT_NAME,
            "app": discovery_info.slug,
        }
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a new connection or migration without copying URL or token."""
        if user_input is not None:
            app = (self.context.get("title_placeholders") or {}).get("app")
            # Supervisor deduplicates identical announcements. Restarting the
            # app does not necessarily replace a stale pending HA flow, so
            # refresh its private credentials from Supervisor on each retry.
            matches = [item for item in await async_app_discoveries(self.hass) if item.slug == app]
            if len(matches) == 1:
                try:
                    self._discovered_service = _discovered_connection(matches[0])
                except ValueError:
                    return self.async_abort(reason="invalid_service_discovery")
        data = self._discovered_service
        if data is None:
            return self.async_abort(reason="invalid_service_discovery")
        errors: dict[str, str] = {}
        client = RemoteClient(
            async_get_clientsession(self.hass),
            data[CONF_SERVER_URL],
            data[CONF_SERVER_TOKEN],
        )
        try:
            await client.async_connect()
            serial = client.identity.serial_number
            if not serial:
                errors["base"] = "service_discovery_requires_serial"
            elif (
                self._discovered_serial is not None
                and self._discovered_serial != serial
            ):
                return self.async_abort(reason="service_identity_changed")
            else:
                self._discovered_serial = serial
                entry = await self.async_set_unique_id(serial)
                if self.context.get("source") == "reconfigure":
                    self._abort_if_unique_id_mismatch()
                if entry is not None and entry.source == "ignore":
                    return self.async_abort(reason="already_configured")
                complete = {**data, CONF_SERIAL_NUMBER: serial}
                if (
                    entry is not None
                    and entry.data.get(CONF_SERVER_URL) == data[CONF_SERVER_URL]
                ):
                    if dict(entry.data) != complete:
                        return self._update_discovered_entry(entry, complete)
                    return self.async_abort(reason="already_configured")
                if user_input is not None:
                    if entry is not None:
                        return self._update_discovered_entry(entry, complete)
                    return self.async_create_entry(
                        title=DEFAULT_NAME,
                        data=complete,
                    )
        except ServiceConnectionError as err:
            _LOGGER.warning("Discovered Modbus app: %s", err)
            errors["base"] = err.reason
        except AmtronBusError:
            errors["base"] = "cannot_connect_service"
        finally:
            await client.async_close()
        return self.async_show_form(step_id="hassio_confirm", errors=errors)

    def _update_discovered_entry(
        self, entry: ConfigEntry, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Preserve IDs/options and let the existing listener reload exactly once."""
        if entry.update_listeners:
            return self.async_update_and_abort(
                entry, data=data, reason="service_discovery_updated"
            )
        return self.async_update_reload_and_abort(
            entry, data=data, reason="service_discovery_updated"
        )

    async def _async_serial_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            config = _serial_config(user_input)
            try:
                return await self._async_test_and_finish(step_id, user_input, config)
            except AmtronConnectionError as err:
                _LOGGER.debug("Connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            except AmtronBusError as err:
                _LOGGER.debug("Identity read failed: %s", err)
                errors["base"] = "invalid_response"

            # Something is wrong with the address or the bus parameters. The
            # wallbox can be anywhere in the documented ranges, so offer to
            # look instead of making the user guess.
            if user_input.get(CONF_SEARCH, SEARCH_OFF) != SEARCH_OFF:
                return self._async_start_search(step_id, user_input)

        return await self._async_show_bus_form(step_id, user_input, errors)

    async def _async_test_and_finish(
        self, step_id: str, user_input: dict[str, Any], config: ConnectionConfig,
        *, gateway: GatewayChange | None = None,
    ) -> ConfigFlowResult:
        entry = (
            self._get_reconfigure_entry()
            if step_id in RECONFIGURE_STEPS
            else None
        )
        loaded = entry is not None and entry.state is ConfigEntryState.LOADED
        original_data = dict(entry.data) if gateway is not None and entry is not None else None
        saved_data: dict[str, Any] | None = None
        if loaded and entry is not None and gateway is None and config == entry.runtime_data.client.config:
            # The existing client owns the bus connection and serializes reads
            # with polling and heartbeat; an unchanged form needs no new client.
            identity = await async_read_identity(entry.runtime_data.client)
            return await self._async_finish(step_id, user_input, identity, config=config)
        if (
            loaded
            and entry is not None
            and not await self.hass.config_entries.async_unload(entry.entry_id)
        ):
            raise AmtronConnectionError("cannot release the active connection")
        async def finish() -> ConfigFlowResult:
            nonlocal saved_data
            identity = await async_test_connection(config)
            result = await self._async_finish(
                step_id, user_input, identity, config=config, defer_reload=gateway is not None,
            )
            if original_data is not None and entry is not None:
                saved_data = dict(entry.data)
            return result

        try:
            result = await gateway.async_apply(finish) if gateway is not None else await finish()
            if gateway is not None and entry is not None and result.get("reason") == "reconfigure_successful":
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return result
        except BaseException as err:
            if original_data is not None and entry is not None:
                if dict(entry.data) != (saved_data if saved_data is not None else original_data):
                    # Another actor changed the entry while commit was pending;
                    # do not overwrite it or resume a now-ambiguous connection.
                    raise GatewayRollbackError from err
                if saved_data is not None and saved_data != original_data:
                    self.hass.config_entries.async_update_entry(entry, data=original_data)
            # A failed probe, wrong device or cancelled flow keeps the old entry
            # usable. On success the flow's update/reload helper starts it instead.
            if loaded and entry is not None and not isinstance(err, GatewayRollbackError):
                await self.hass.config_entries.async_setup(entry.entry_id)
            raise

    async def _async_search_bus(
        self, base: SerialConfig, all_parameters: bool
    ) -> SearchResult | None:
        entry = (
            self._get_reconfigure_entry()
            if self._pending_step in ("reconfigure", "serial_reconfigure")
            else None
        )
        loaded = entry is not None and entry.state is ConfigEntryState.LOADED
        if (
            loaded
            and entry is not None
            and not await self.hass.config_entries.async_unload(entry.entry_id)
        ):
            raise AmtronConnectionError("cannot release the active serial port")
        try:
            return await async_search(base, all_parameters=all_parameters)
        finally:
            # Search results need user acceptance. Restore the original entry
            # while that form is open, also when the search was cancelled.
            if loaded and entry is not None:
                await self.hass.config_entries.async_setup(entry.entry_id)

    async def _async_show_bus_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        schema = user_schema(await self._async_port_options())
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema, self._suggested_values(step_id, user_input)
            ),
            errors=errors,
            description_placeholders={
                "validated_layout": layout_label(VALIDATED_LAYOUT)
            },
        )

    # --- searching the bus -------------------------------------------------

    def _async_start_search(
        self, step_id: str, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        self._pending_step = step_id
        self._pending_input = dict(user_input)
        base = _serial_config(user_input)
        all_parameters = user_input.get(CONF_SEARCH) == SEARCH_FULL
        self._search_task = self.hass.async_create_task(
            self._async_search_bus(base, all_parameters),
            name="mennekes_amtron_rtu_bus_search",
        )
        return self._async_show_search_progress(base, all_parameters)

    def _async_show_search_progress(
        self, base: SerialConfig, all_parameters: bool
    ) -> ConfigFlowResult:
        candidates = len(search_space(base, all_parameters=all_parameters))
        return self.async_show_progress(
            step_id="search",
            progress_action="searching",
            progress_task=self._search_task,
            description_placeholders={
                "candidates": str(candidates),
                "minutes": str(
                    max(
                        1,
                        math.ceil(
                            candidates * PROBE_TIMEOUT * (PROBE_RETRIES + 1) / 60
                        ),
                    )
                ),
            },
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the bus search to finish."""

        task = self._search_task
        if task is not None and not task.done():
            base = _serial_config(self._pending_input or {})
            all_parameters = (self._pending_input or {}).get(CONF_SEARCH) == SEARCH_FULL
            return self._async_show_search_progress(base, all_parameters)

        self._search_result = None
        self._search_error = "not_found"
        if task is not None:
            try:
                self._search_result = task.result()
            except AmtronConnectionError as err:
                # The search gave up on the port itself, not on the
                # candidates, so say that rather than "nothing answered".
                _LOGGER.debug("Search could not use the port: %s", err)
                self._search_error = "cannot_connect"
            except AmtronBusError as err:  # pragma: no cover - defensive
                _LOGGER.debug("Search failed: %s", err)
        self._search_task = None
        return self.async_show_progress_done(next_step_id="search_result")

    async def async_step_search_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report what the search found and let the user accept it."""

        result = self._search_result
        if result is None:
            return await self._async_show_bus_form(
                self._pending_step, self._pending_input, {"base": self._search_error}
            )

        if user_input is None:
            return self.async_show_form(
                step_id="search_result",
                description_placeholders={
                    "device_id": str(result.config.device_id),
                    "baudrate": str(result.config.baudrate),
                    "frame": _frame(result.config),
                    "layout": layout_label(result.identity.layout_version),
                    "serial_number": display_identifier(result.identity.serial_number)
                    or "-",
                    "attempts": str(result.attempts),
                },
            )

        return await self._async_finish(
            self._pending_step, _serial_input(result.config), result.identity
        )

    # --- finishing ---------------------------------------------------------

    async def _async_finish(
        self,
        step_id: str,
        user_input: dict[str, Any],
        identity: DeviceIdentity,
        *,
        config: ConnectionConfig | None = None,
        defer_reload: bool = False,
    ) -> ConfigFlowResult:
        if config is None:
            config = _serial_config(user_input)
        data = (
            _network_input(config)
            if isinstance(config, NetworkConfig)
            else _serial_input(config)
        )
        if identity.serial_number:
            data[CONF_SERIAL_NUMBER] = identity.serial_number
            await self.async_set_unique_id(identity.serial_number)
        else:
            # Older firmware has no serial register. A network gateway may
            # expose several unit IDs; distinguish those endpoints as well.
            suffix = (
                f"{config.port}_{config.device_id}"
                if isinstance(config, NetworkConfig)
                else config.port
            )
            await self.async_set_unique_id(f"{DOMAIN}_{suffix}")

        if step_id in RECONFIGURE_STEPS:
            self._abort_if_unique_id_mismatch()
            entry = self._get_reconfigure_entry()
            if defer_reload:
                # The old client is unloaded. Publish the new entry data, but
                # do not start its connection until adapter commit succeeds.
                return self.async_update_and_abort(entry, data=data)
            if not entry.update_listeners:
                # An unloaded or failed setup has no listener to start it.
                return self.async_update_reload_and_abort(entry, data=data)
            # The entry's update listener owns reloading, including options.
            # A second reload here races it and is rejected from HA 2026.12.
            return self.async_update_and_abort(entry, data=data)

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=_entry_title(identity), data=data)

    def _suggested_values(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return what the form should already contain.

        A failed connection test must not throw away what the user typed, and
        reconfiguring must start from the entry's current bus parameters
        rather than from the factory defaults.
        """

        if user_input is not None:
            return {key: value for key, value in user_input.items() if key != CONF_TCP_AUTH_PASSWORD}
        if step_id in RECONFIGURE_STEPS:
            data = dict(self._get_reconfigure_entry().data)
            data.pop(CONF_TCP_AUTH_PASSWORD, None)
            if step_id == "network_reconfigure":
                # Serial/service data must not preselect an invalid LAN protocol.
                if data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL) == CONNECTION_SERIAL:
                    data.pop(CONF_CONNECTION_TYPE, None)
                return data
            config = _serial_config(data)
            return {
                **data,
                CONF_FRAME: frame_label(
                    config.bytesize, config.parity, config.stopbits
                ),
            }
        return {} if step_id == "network" else {CONF_FRAME: DEFAULT_FRAME}

    async def _async_port_options(self) -> list[Any]:
        by_id = await self.hass.async_add_executor_job(list_by_id_ports)
        comports = await self.hass.async_add_executor_job(list_comports)
        return merge_port_options(by_id, comports)

    @staticmethod
    def async_get_options_flow(
        config_entry: MennekesAmtronConfigEntry,
    ) -> OptionsFlow:
        """Return the options flow of this integration."""

        return MennekesAmtronOptionsFlow()


def _serial_config(user_input: dict[str, Any]) -> SerialConfig:
    """Return the bus configuration a form or an entry describes.

    The form asks for one frame, because the device documents exactly three.
    A stored entry keeps the data bits, parity and stop bits separately, so
    both shapes are accepted and no entry needs migrating.
    """

    if CONF_FRAME in user_input:
        bytesize, parity, stopbits = frame_parts(str(user_input[CONF_FRAME]))
    else:
        bytesize = int(user_input.get(CONF_BYTESIZE, DEFAULT_BYTESIZE))
        parity = str(user_input.get(CONF_PARITY, DEFAULT_PARITY))
        stopbits = int(user_input.get(CONF_STOPBITS, DEFAULT_STOPBITS))
    return SerialConfig(
        port=str(user_input.get(CONF_PORT, "")).strip(),
        baudrate=int(user_input.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)),
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        device_id=int(user_input.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)),
    )


def _serial_input(config: SerialConfig) -> dict[str, Any]:
    """Return the entry data for a serial configuration."""

    return {
        CONF_CONNECTION_TYPE: CONNECTION_SERIAL,
        CONF_PORT: config.port,
        CONF_DEVICE_ID: config.device_id,
        CONF_BAUDRATE: config.baudrate,
        CONF_BYTESIZE: config.bytesize,
        CONF_PARITY: config.parity,
        CONF_STOPBITS: config.stopbits,
    }


def _network_config(user_input: dict[str, Any]) -> NetworkConfig:
    """Use the same endpoint validation as the independent service."""

    return NetworkConfig(
        host=user_input.get(CONF_HOST, ""),
        tcp_port=user_input.get(CONF_TCP_PORT, DEFAULT_TCP_PORT),
        connection_type=user_input.get(CONF_CONNECTION_TYPE, CONNECTION_RTU_OVER_TCP),
        device_id=user_input.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
        tcp_authentication=user_input.get(CONF_TCP_AUTHENTICATION, False),
        tcp_auth_password=user_input.get(CONF_TCP_AUTH_PASSWORD, ""),
    )


def _network_input(config: NetworkConfig) -> dict[str, Any]:
    """Persist only settings belonging to the selected network transport."""

    return {
        CONF_CONNECTION_TYPE: config.connection_type,
        CONF_HOST: config.host,
        CONF_TCP_PORT: config.tcp_port,
        CONF_DEVICE_ID: config.device_id,
        **({
            CONF_TCP_AUTHENTICATION: True,
            CONF_TCP_AUTH_PASSWORD: config.tcp_auth_password,
        } if config.tcp_authentication else {}),
    }


def _frame(config: SerialConfig) -> str:
    """Return the frame for the user to read, not the selector option."""

    return frame_display(config.bytesize, config.parity, config.stopbits)


def _entry_title(identity: DeviceIdentity) -> str:
    """Return the entry title, which also becomes the device name.

    The device name is prefixed to every entity name and every entity id, so
    it stays short and carries no serial number. The article number and the
    serial are shown on the device page through ``device_info`` instead, and
    the serial is what makes the entry unique.
    """

    del identity
    return DEFAULT_NAME
