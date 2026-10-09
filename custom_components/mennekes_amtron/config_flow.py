"""Config flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow

from ._flow_connect import async_test_connection
from ._flow_search import SearchResult, async_search, search_space
from ._flow_serial import list_by_id_ports, list_comports, merge_port_options
from .client import SerialConfig
from .client_errors import AmtronBusError, AmtronConnectionError
from .config_schemas import user_schema
from .const import (
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
    DEFAULT_MODEL,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DOMAIN,
    MANUFACTURER,
    SEARCH_FULL,
    SEARCH_OFF,
    frame_display,
    frame_label,
    frame_parts,
)
from .data import DeviceIdentity
from .entry_types import MennekesAmtronConfigEntry
from .options_flow import MennekesAmtronOptionsFlow
from .registers import VALIDATED_LAYOUT, layout_label

_LOGGER = logging.getLogger(__name__)

BUS_FIELDS = (
    CONF_PORT,
    CONF_DEVICE_ID,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_STOPBITS,
)


class MennekesAmtronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the serial setup of one wallbox."""

    VERSION = 1

    def __init__(self) -> None:
        self._search_task: Any = None
        self._search_result: SearchResult | None = None
        self._search_error = "not_found"
        self._pending_input: dict[str, Any] | None = None
        self._pending_step: str = "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the serial parameters and verify them on the bus."""

        return await self._async_serial_step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the serial parameters of an existing entry."""

        return await self._async_serial_step("reconfigure", user_input)

    async def _async_serial_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            config = _serial_config(user_input)
            try:
                identity = await async_test_connection(config)
            except AmtronConnectionError as err:
                _LOGGER.debug("Connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            except AmtronBusError as err:
                _LOGGER.debug("Identity read failed: %s", err)
                errors["base"] = "invalid_response"
            else:
                return await self._async_finish(step_id, user_input, identity)

            # Something is wrong with the address or the bus parameters. The
            # wallbox can be anywhere in the documented ranges, so offer to
            # look instead of making the user guess.
            if user_input.get(CONF_SEARCH, SEARCH_OFF) != SEARCH_OFF:
                return self._async_start_search(step_id, user_input)

        return await self._async_show_bus_form(step_id, user_input, errors)

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
            async_search(base, all_parameters=all_parameters),
            name="mennekes_amtron_bus_search",
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
                "minutes": f"{candidates * 0.5 / 60:.0f}",
            },
        )

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the bus search to finish."""

        task = self._search_task
        if task is not None and not task.done():
            base = _serial_config(self._pending_input or {})
            all_parameters = (
                (self._pending_input or {}).get(CONF_SEARCH) == SEARCH_FULL
            )
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
                    "serial_number": result.identity.serial_number or "-",
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

        if step_id == "reconfigure":
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=data
            )

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
        if step_id == "reconfigure":
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
    return f"{MANUFACTURER} {DEFAULT_MODEL}"
