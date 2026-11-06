"""Options flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from .config_schemas import options_schema
from .device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
)
from .remote import CONF_SERVER_URL


class MennekesAmtronOptionsFlow(OptionsFlow):
    """Let the user choose the control mode, poll interval and current cap."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the entry options."""
        step_id = "service" if CONF_SERVER_URL in self.config_entry.data else "init"
        return await self._async_options(step_id, user_input)

    async def async_step_service(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure cache refresh and the additional HA current cap."""
        return await self._async_options("service", user_input)

    async def _async_options(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:

        if user_input is not None:
            options = _normalized(user_input, service_mode=step_id == "service")
            return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id=step_id,
            data_schema=options_schema(
                dict(self.config_entry.options),
                max_current=_max_current(self.config_entry),
                service_mode=step_id == "service",
            ),
        )


def _normalized(user_input: dict[str, Any], *, service_mode: bool) -> dict[str, Any]:
    options: dict[str, Any] = {
        CONF_SCAN_INTERVAL_SECONDS: int(user_input[CONF_SCAN_INTERVAL_SECONDS]),
        CONF_CURRENT_LIMIT: float(user_input[CONF_CURRENT_LIMIT]),
    }
    if not service_mode:
        options[CONF_CONTROL_MODE] = str(user_input[CONF_CONTROL_MODE])
        options[CONF_MEASUREMENT_INTERVAL_SECONDS] = int(
            user_input.get(
                CONF_MEASUREMENT_INTERVAL_SECONDS, DEFAULT_MEASUREMENT_INTERVAL_SECONDS
            )
        )
        options[CONF_STATE_CHANGE_INTERVAL_SECONDS] = int(
            user_input.get(
                CONF_STATE_CHANGE_INTERVAL_SECONDS, DEFAULT_STATE_CHANGE_INTERVAL_SECONDS
            )
        )
        options[CONF_PHASE_SWITCH_GRACE_SECONDS] = int(
            user_input.get(
                CONF_PHASE_SWITCH_GRACE_SECONDS, DEFAULT_PHASE_SWITCH_GRACE_SECONDS
            )
        )
    return options


def _max_current(entry: Any) -> float:
    """Return the highest current the device reported, or a safe default."""

    runtime_data = getattr(entry, "runtime_data", None)
    identity = getattr(runtime_data, "identity", None)
    maximum = getattr(identity, "max_evse_current", None)
    if isinstance(maximum, (int, float)) and maximum >= 6:
        return float(maximum)
    return 16.0


def control_mode_is_master(options: dict[str, Any]) -> bool:
    """Return true when the entry is allowed to act as the Modbus master."""

    return options.get(CONF_CONTROL_MODE) == CONTROL_MODE_MASTER
