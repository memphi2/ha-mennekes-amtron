"""Options flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from .config_schemas import options_schema
from .const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
)


class MennekesAmtronOptionsFlow(OptionsFlow):
    """Let the user choose the control mode, poll interval and current cap."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the entry options."""

        if user_input is not None:
            return self.async_create_entry(data=_normalized(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema(
                dict(self.config_entry.options),
                max_current=_max_current(self.config_entry),
            ),
        )


def _normalized(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_CONTROL_MODE: str(user_input[CONF_CONTROL_MODE]),
        CONF_SCAN_INTERVAL_SECONDS: int(user_input[CONF_SCAN_INTERVAL_SECONDS]),
        CONF_CURRENT_LIMIT: float(user_input[CONF_CURRENT_LIMIT]),
    }


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
