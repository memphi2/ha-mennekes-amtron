"""Config flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow

from ._flow_connect import async_test_connection
from ._flow_serial import list_by_id_ports, list_comports, merge_port_options
from .client import SerialConfig
from .client_errors import AmtronBusError, AmtronConnectionError
from .config_schemas import user_schema
from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_ID,
    CONF_PARITY,
    CONF_PORT,
    CONF_SERIAL_NUMBER,
    CONF_STOPBITS,
    DEFAULT_MODEL,
    DOMAIN,
    MANUFACTURER,
)
from .entry_types import MennekesAmtronConfigEntry
from .options_flow import MennekesAmtronOptionsFlow
from .registers import VALIDATED_LAYOUT, layout_label

_LOGGER = logging.getLogger(__name__)


class MennekesAmtronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the serial setup of one wallbox."""

    VERSION = 1

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

        return self.async_show_form(
            step_id=step_id,
            data_schema=user_schema(await self._async_port_options()),
            errors=errors,
            description_placeholders={
                "validated_layout": layout_label(VALIDATED_LAYOUT)
            },
        )

    async def _async_finish(
        self,
        step_id: str,
        user_input: dict[str, Any],
        identity: Any,
    ) -> ConfigFlowResult:
        config = _serial_config(user_input)
        data = {**user_input, CONF_PORT: config.port}
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
    return SerialConfig(
        port=str(user_input[CONF_PORT]).strip(),
        baudrate=int(user_input[CONF_BAUDRATE]),
        bytesize=int(user_input[CONF_BYTESIZE]),
        parity=str(user_input[CONF_PARITY]),
        stopbits=int(user_input[CONF_STOPBITS]),
        device_id=int(user_input[CONF_DEVICE_ID]),
    )


def _entry_title(identity: Any) -> str:
    title = f"{MANUFACTURER} {DEFAULT_MODEL}"
    if identity.article_number:
        title = f"{title} {identity.article_number}"
    if identity.serial_number:
        return f"{title} ({identity.serial_number})"
    return title
