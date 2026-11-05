"""Config flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

import logging
import math
from typing import Any

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
from ._flow_search import (
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    SearchResult,
    async_search,
    search_space,
)
from ._flow_serial import list_by_id_ports, list_comports, merge_port_options
from .config_schemas import service_schema, user_schema
from .data import DeviceIdentity
from .device.client import SerialConfig
from .device.client_errors import AmtronBusError, AmtronConnectionError
from .device.const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_ID,
    CONF_FRAME,
    CONF_PARITY,
    CONF_PORT,
    CONF_SEARCH,
    CONF_SERIAL_NUMBER,
    CONF_STOPBITS,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_FRAME,
    DEFAULT_NAME,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DOMAIN,
    SEARCH_FULL,
    SEARCH_OFF,
    frame_display,
    frame_label,
    frame_parts,
)
from .device.identity import async_read_identity, display_identifier
from .device.registers import VALIDATED_LAYOUT, layout_label
from .device.service_config import validate_token
from .device.service_discovery import APP_HOSTNAME
from .entry_types import MennekesAmtronConfigEntry
from .options_flow import MennekesAmtronOptionsFlow
from .remote import CONF_SERVER_TOKEN, CONF_SERVER_URL, RemoteClient, server_url
from .remote_errors import ServiceConnectionError
from .supervisor_discovery import async_app_discoveries

_LOGGER = logging.getLogger(__name__)

BUS_FIELDS = (
    CONF_PORT,
    CONF_DEVICE_ID,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_STOPBITS,
)


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
    """Handle the serial setup of one wallbox."""

    VERSION = 1

    def __init__(self) -> None:
        self._search_task: Any = None
        self._search_result: SearchResult | None = None
        self._search_error = "not_found"
        self._pending_input: dict[str, Any] | None = None
        self._pending_step: str = "serial"
        self._discovered_service: dict[str, Any] | None = None
        self._discovered_serial: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the independent service or a direct serial connection."""
        return self.async_show_menu(step_id="user", menu_options=["server", "serial"])

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_serial_step("serial", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["server", "serial_reconfigure"]
        )

    async def async_step_serial_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_serial_step("serial_reconfigure", user_input)

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
        self, step_id: str, user_input: dict[str, Any], config: SerialConfig
    ) -> ConfigFlowResult:
        entry = (
            self._get_reconfigure_entry()
            if step_id in ("reconfigure", "serial_reconfigure")
            else None
        )
        loaded = entry is not None and entry.state is ConfigEntryState.LOADED
        if loaded and entry is not None and config == entry.runtime_data.client.config:
            # The existing client owns the exclusive port and serializes reads
            # with polling and heartbeat; an unchanged form needs no new client.
            identity = await async_read_identity(entry.runtime_data.client)
            return await self._async_finish(step_id, user_input, identity)
        if (
            loaded
            and entry is not None
            and not await self.hass.config_entries.async_unload(entry.entry_id)
        ):
            raise AmtronConnectionError("cannot release the active serial port")
        try:
            identity = await async_test_connection(config)
            return await self._async_finish(step_id, user_input, identity)
        except BaseException:
            # A failed probe, wrong device or cancelled flow keeps the old entry
            # usable. On success the flow's update/reload helper starts it instead.
            if loaded and entry is not None:
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
    ) -> ConfigFlowResult:
        config = _serial_config(user_input)
        data = {**_serial_input(config)}
        if identity.serial_number:
            data[CONF_SERIAL_NUMBER] = identity.serial_number
            await self.async_set_unique_id(identity.serial_number)
        else:
            # Older firmware has no serial register; the port path is then the
            # only stable thing that distinguishes two wallboxes.
            await self.async_set_unique_id(f"{DOMAIN}_{config.port}")

        if step_id in ("reconfigure", "serial_reconfigure"):
            self._abort_if_unique_id_mismatch()
            entry = self._get_reconfigure_entry()
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
            return dict(user_input)
        if step_id in ("reconfigure", "serial_reconfigure"):
            data = dict(self._get_reconfigure_entry().data)
            config = _serial_config(data)
            return {
                **data,
                CONF_FRAME: frame_label(
                    config.bytesize, config.parity, config.stopbits
                ),
            }
        return {CONF_FRAME: DEFAULT_FRAME}

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
        CONF_PORT: config.port,
        CONF_DEVICE_ID: config.device_id,
        CONF_BAUDRATE: config.baudrate,
        CONF_BYTESIZE: config.bytesize,
        CONF_PARITY: config.parity,
        CONF_STOPBITS: config.stopbits,
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
