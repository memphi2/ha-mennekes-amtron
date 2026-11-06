"""Options flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.core import valid_entity_id

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
from .vehicle_soc_const import (
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_SOC_ENTITY,
    VEHICLE_CHARGE_LIMIT_DOMAINS,
    VEHICLE_SOC_DOMAINS,
)


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

        errors = _vehicle_errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            options = _normalized(user_input, service_mode=step_id == "service")
            return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id=step_id,
            data_schema=options_schema(
                dict(self.config_entry.options) if user_input is None else user_input,
                max_current=_max_current(self.config_entry),
                service_mode=step_id == "service",
            ),
            errors=errors,
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
    for key in (CONF_VEHICLE_SOC_ENTITY, CONF_VEHICLE_CHARGE_LIMIT_ENTITY):
        if value := user_input.get(key):
            options[key] = value
    return options


def _vehicle_errors(values: dict[str, Any]) -> dict[str, str]:
    """Validate source identifiers without requiring a currently online car."""
    errors = {}
    for key, domains in (
        (CONF_VEHICLE_SOC_ENTITY, VEHICLE_SOC_DOMAINS),
        (CONF_VEHICLE_CHARGE_LIMIT_ENTITY, VEHICLE_CHARGE_LIMIT_DOMAINS),
    ):
        if not (value := values.get(key)):
            continue
        if (
            not isinstance(value, str) or not valid_entity_id(value)
            or value.split(".", 1)[0] not in domains
        ):
            errors[key] = "invalid_vehicle_entity"
    if values.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY) and not values.get(CONF_VEHICLE_SOC_ENTITY):
        errors[CONF_VEHICLE_SOC_ENTITY] = "vehicle_soc_required"
    return errors


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
