"""Options flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

import math
import re
from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.core import valid_entity_id

from .bavarian_history import bavarian_vehicle_vin
from .charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_AUTO_MATCH,
    CONF_LOSS_BAVARIAN_DEVICE,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    LOSS_SOURCE_BAVARIANDATA,
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCE_SENSOR,
    LOSS_SOURCES,
)
from .charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_MODULE,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_CHARGING_PRICE,
    CONF_CHARGING_PRICE_ENTITY,
    CONF_HIGH_NETWORK,
    CONF_HIGH_PERIODS,
    CONF_LOW_NETWORK,
    CONF_LOW_PERIODS,
    CONF_PRICE_INCLUDES_NETWORK,
    CONF_REGULAR_NETWORK,
    CONF_TARIFF_YEAR,
    COST_SOURCE_DISABLED,
    COST_SOURCE_FIXED,
    COST_SOURCE_SENSOR,
    COST_SOURCES,
    NETWORK_MODULE_3,
    NETWORK_MODULE_NONE,
    NETWORK_MODULES,
)
from .config_schemas import (
    cost_network_schema,
    cost_source_schema,
    loss_bavariandata_schema,
    loss_sensor_schema,
    options_schema,
)
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

    def __init__(self) -> None:
        """Keep changes private until the selected source is configured."""
        super().__init__()
        self._pending_options: dict[str, Any] | None = None
        self._pending_step: str | None = None

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

        self._pending_options = None
        self._pending_step = None
        errors = _vehicle_errors(user_input) if user_input is not None else {}
        source = (user_input or {}).get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED)
        if source not in LOSS_SOURCES:
            errors[CONF_CHARGE_LOSS_SOURCE] = "loss_source_invalid"
        cost_source = (user_input or {}).get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED)
        if cost_source not in COST_SOURCES:
            errors[CONF_CHARGING_COST_SOURCE] = "cost_source_invalid"
        if user_input is not None and not errors:
            options = _normalized(user_input, service_mode=step_id == "service")
            if cost_source != COST_SOURCE_DISABLED:
                options[CONF_CHARGING_COST_SOURCE] = cost_source
            self._pending_options = options
            if source != LOSS_SOURCE_DISABLED:
                options[CONF_CHARGE_LOSS_SOURCE] = source
                self._pending_step = f"loss_{source}"
                return await self._async_loss_options(source, None)
            return await self._async_start_cost_options(options)

        return self.async_show_form(
            step_id=step_id,
            data_schema=options_schema(
                dict(self.config_entry.options) if user_input is None else user_input,
                max_current=_max_current(self.config_entry),
                service_mode=step_id == "service",
            ),
            errors=errors,
        )

    async def async_step_loss_bavariandata(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use a vehicle's existing BavarianData charging history."""
        return await self._async_loss_options(LOSS_SOURCE_BAVARIANDATA, user_input)

    async def async_step_loss_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure estimated energy and explicitly opt into automatic matching."""
        return await self._async_loss_options(LOSS_SOURCE_SENSOR, user_input)

    async def _async_loss_options(
        self, source: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        pending = self._pending_options
        if pending is None or self._pending_step != f"loss_{source}":
            return await self.async_step_init()
        errors = _loss_errors(source, user_input) if user_input is not None else {}
        if (
            user_input is not None and not errors and source == LOSS_SOURCE_BAVARIANDATA
            and bavarian_vehicle_vin(self.hass, user_input[CONF_LOSS_BAVARIAN_DEVICE]) is None
        ):
            errors[CONF_LOSS_BAVARIAN_DEVICE] = "loss_source_invalid"
        if user_input is not None and not errors:
            if source == LOSS_SOURCE_BAVARIANDATA:
                details = {CONF_LOSS_BAVARIAN_DEVICE: user_input[CONF_LOSS_BAVARIAN_DEVICE]}
            else:
                details = {
                    CONF_VEHICLE_LAST_CHARGE_ENERGY: user_input[CONF_VEHICLE_LAST_CHARGE_ENERGY],
                    CONF_LOSS_AUTO_MATCH: user_input.get(CONF_LOSS_AUTO_MATCH, False),
                }
            pending.update(details)
            return await self._async_start_cost_options(pending)
        schema = loss_bavariandata_schema if source == LOSS_SOURCE_BAVARIANDATA else loss_sensor_schema
        return self.async_show_form(
            step_id=f"loss_{source}",
            data_schema=schema(dict(self.config_entry.options) if user_input is None else user_input),
            errors=errors,
        )

    async def _async_start_cost_options(self, options: dict[str, Any]) -> ConfigFlowResult:
        """Continue a private draft after the optional loss-source step."""
        source = options.get(CONF_CHARGING_COST_SOURCE)
        if source is None:
            return self._finish_options(options)
        self._pending_step = f"cost_{source}"
        return await self._async_cost_options(source, None)

    async def async_step_cost_fixed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a fixed gross electricity price."""
        return await self._async_cost_options(COST_SOURCE_FIXED, user_input)

    async def async_step_cost_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a sensor providing the current gross electricity price."""
        return await self._async_cost_options(COST_SOURCE_SENSOR, user_input)

    async def _async_cost_options(
        self, source: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        pending = self._pending_options
        if (
            pending is None
            or pending.get(CONF_CHARGING_COST_SOURCE) != source
            or self._pending_step not in (f"cost_{source}", "cost_network")
        ):
            return await self.async_step_init()
        errors = _cost_errors(source, user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            if source == COST_SOURCE_FIXED:
                pending[CONF_CHARGING_PRICE] = float(user_input[CONF_CHARGING_PRICE])
            else:
                pending[CONF_CHARGING_PRICE_ENTITY] = user_input[CONF_CHARGING_PRICE_ENTITY]
            module = user_input.get(CONF_CHARGING_NETWORK_MODULE, NETWORK_MODULE_NONE)
            pending[CONF_CHARGING_NETWORK_TARIFF] = {"module": module}
            if module == NETWORK_MODULE_NONE:
                return self._finish_options(pending)
            self._pending_step = "cost_network"
            return await self.async_step_cost_network()
        current = dict(self.config_entry.options) if user_input is None else user_input
        if user_input is None:
            # Back navigation from Module 3 must show the draft that the user
            # just entered, not the older saved options.
            current.update(pending)
            previous = current.get(CONF_CHARGING_NETWORK_TARIFF)
            current[CONF_CHARGING_NETWORK_MODULE] = (
                previous.get("module", NETWORK_MODULE_NONE)
                if isinstance(previous, dict) else NETWORK_MODULE_NONE
            )
        return self.async_show_form(
            step_id=f"cost_{source}",
            data_schema=cost_source_schema(current, source=source),
            errors=errors,
        )

    async def async_step_cost_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate a published network tariff before committing any options."""
        pending = self._pending_options
        if pending is None or self._pending_step != "cost_network":
            return await self.async_step_init()
        module = pending[CONF_CHARGING_NETWORK_TARIFF]["module"]
        errors = {}
        if user_input is not None:
            try:
                pending[CONF_CHARGING_NETWORK_TARIFF] = _network_tariff(
                    user_input, module=module,
                    timezone=self.hass.config.time_zone,
                )
            except (KeyError, TypeError, ValueError):
                errors["base"] = "network_tariff_invalid"
            else:
                return self._finish_options(pending)
        previous = dict(self.config_entry.options).get(CONF_CHARGING_NETWORK_TARIFF)
        current = (
            _network_form_values(previous if isinstance(previous, dict) else {})
            if user_input is None else user_input
        )
        return self.async_show_form(
            step_id="cost_network",
            data_schema=cost_network_schema(current, module=module),
            description_placeholders={"module": "2" if module != NETWORK_MODULE_3 else "3"},
            errors=errors,
        )

    def _finish_options(self, options: dict[str, Any]) -> ConfigFlowResult:
        """Commit the fully configured draft once, without stale source fields."""
        self._pending_options = None
        self._pending_step = None
        return self.async_create_entry(data=options)


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


def _loss_errors(source: str, values: dict[str, Any]) -> dict[str, str]:
    """Validate identifiers without polling a vehicle or requiring it online."""
    if source == LOSS_SOURCE_BAVARIANDATA:
        value = values.get(CONF_LOSS_BAVARIAN_DEVICE)
        return {} if isinstance(value, str) and value.strip() else {
            CONF_LOSS_BAVARIAN_DEVICE: "loss_source_invalid"
        }
    errors = {}
    value = values.get(CONF_VEHICLE_LAST_CHARGE_ENERGY)
    if not isinstance(value, str) or not valid_entity_id(value) or not value.startswith("sensor."):
        errors[CONF_VEHICLE_LAST_CHARGE_ENERGY] = "loss_source_invalid"
    if type(values.get(CONF_LOSS_AUTO_MATCH, False)) is not bool:
        errors[CONF_LOSS_AUTO_MATCH] = "loss_source_invalid"
    return errors


def _cost_errors(source: str, values: dict[str, Any]) -> dict[str, str]:
    errors = {}
    if source == COST_SOURCE_FIXED:
        value = values.get(CONF_CHARGING_PRICE)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > 1000 or not math.isfinite(value):
            errors[CONF_CHARGING_PRICE] = "cost_price_invalid"
    else:
        value = values.get(CONF_CHARGING_PRICE_ENTITY)
        if not isinstance(value, str) or not valid_entity_id(value) or not value.startswith("sensor."):
            errors[CONF_CHARGING_PRICE_ENTITY] = "cost_source_invalid"
    if values.get(CONF_CHARGING_NETWORK_MODULE, NETWORK_MODULE_NONE) not in NETWORK_MODULES:
        errors[CONF_CHARGING_NETWORK_MODULE] = "network_tariff_invalid"
    return errors


def _network_tariff(values: dict[str, Any], *, module: str, timezone: str) -> dict[str, Any]:
    """Convert form controls into the model's strict tariff representation."""
    from .device.charging_cost import TariffConfig

    tariff = {
        "module": module,
        CONF_REGULAR_NETWORK: values[CONF_REGULAR_NETWORK],
        CONF_PRICE_INCLUDES_NETWORK: values[CONF_PRICE_INCLUDES_NETWORK],
    }
    if module == NETWORK_MODULE_3:
        quarters = values[CONF_ACTIVE_QUARTERS]
        if not isinstance(quarters, list) or any(value not in ("1", "2", "3", "4") for value in quarters):
            raise ValueError("invalid active_quarters")
        tariff.update({
            CONF_HIGH_NETWORK: values[CONF_HIGH_NETWORK],
            CONF_LOW_NETWORK: values[CONF_LOW_NETWORK],
            CONF_ACTIVE_QUARTERS: [int(value) for value in quarters],
            CONF_TARIFF_YEAR: values[CONF_TARIFF_YEAR],
            "timezone": timezone,
            "windows": [
                *_tariff_periods(values[CONF_HIGH_PERIODS], "ht"),
                *_tariff_periods(values[CONF_LOW_PERIODS], "nt"),
            ],
        })
    return TariffConfig.parse(tariff).as_dict()


def _tariff_periods(value: object, rate: str) -> list[dict[str, str]]:
    """Parse one HH:MM-HH:MM interval per line, including overnight windows."""
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ValueError("tariff periods must be short text")
    lines = value.splitlines()
    if not 1 <= len(lines) <= 8:
        raise ValueError("tariff periods require one to eight lines")
    windows: list[dict[str, str]] = []
    for line in lines:
        match = re.fullmatch(r"\s*([0-2][0-9]:[0-5][0-9])\s*[-\u2013]\s*([0-2][0-9]:[0-5][0-9])\s*", line)
        if match is None:
            raise ValueError("tariff period must be HH:MM-HH:MM")
        windows.append({"start": match[1], "end": match[2], "rate": rate})
    return windows


def _network_form_values(tariff: dict[str, Any]) -> dict[str, Any]:
    values = dict(tariff)
    quarters = tariff.get(CONF_ACTIVE_QUARTERS)
    values[CONF_ACTIVE_QUARTERS] = [str(value) for value in quarters] if isinstance(quarters, list) else []
    windows = tariff.get("windows")
    windows = windows if isinstance(windows, list) else []
    for rate, key in (("ht", CONF_HIGH_PERIODS), ("nt", CONF_LOW_PERIODS)):
        values[key] = "\n".join(
            f"{window['start']}-{window['end']}"
            for window in windows if isinstance(window, dict)
            and window.get("rate") == rate
            and isinstance(window.get("start"), str)
            and isinstance(window.get("end"), str)
        )
    return values


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
