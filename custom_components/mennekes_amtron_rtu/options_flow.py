"""Options flow for the MENNEKES AMTRON integration."""

from __future__ import annotations

import math
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    CONF_AUTO_TARIFF_REFRESH,
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
from .network_tariff_lookup import async_lookup_home_tariff
from .remote import CONF_SERVER_URL
from .timed_goal_const import (
    CONF_TIMED_BAVARIAN_DEVICE,
    CONF_TIMED_CALENDAR_ENTITY,
    CONF_TIMED_CALENDAR_TITLE,
    CONF_TIMED_CAPACITY_ENTITY,
    CONF_TIMED_CAPACITY_KWH,
    CONF_TIMED_DEPARTURE_ENTITY,
    CONF_TIMED_TIMER1_DAYS,
    CONF_TIMED_TIMER2_DAYS,
    CONF_TIMED_VEHICLE_TIME_ZONE,
    TIMED_WEEKDAYS,
)
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
        self._home_tariff_lookup_done = False
        self._home_tariff_values: dict[str, Any] | None = None
        self._home_tariff_source = "—"

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
        self._home_tariff_lookup_done = False
        self._home_tariff_values = None
        self._home_tariff_source = "—"
        errors = _vehicle_errors(user_input) if user_input is not None else {}
        if user_input is not None:
            errors.update(_timed_errors(user_input))
            if user_input.get(CONF_TIMED_BAVARIAN_DEVICE) and bavarian_vehicle_vin(
                self.hass, user_input[CONF_TIMED_BAVARIAN_DEVICE]
            ) is None:
                errors[CONF_TIMED_BAVARIAN_DEVICE] = "timed_bavarian_invalid"
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
        allow_network = getattr(self.hass.config, "country", None) == "DE"
        errors = (
            _cost_errors(source, user_input, allow_network=allow_network)
            if user_input is not None else {}
        )
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
            data_schema=cost_source_schema(
                current, source=source, allow_network=allow_network,
            ),
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
                if module == NETWORK_MODULE_3 and type(user_input.get(CONF_AUTO_TARIFF_REFRESH, False)) is not bool:
                    raise ValueError("auto tariff refresh must be boolean")
                pending[CONF_CHARGING_NETWORK_TARIFF] = _network_tariff(
                    user_input, module=module,
                    # German network windows use the operator's local clock,
                    # independently of HA's display/configuration time zone.
                    timezone="Europe/Berlin",
                )
            except (KeyError, TypeError, ValueError):
                errors["base"] = "network_tariff_invalid"
            else:
                if module == NETWORK_MODULE_3:
                    pending[CONF_AUTO_TARIFF_REFRESH] = user_input.get(
                        CONF_AUTO_TARIFF_REFRESH, False,
                    )
                return self._finish_options(pending)
        previous = dict(self.config_entry.options).get(CONF_CHARGING_NETWORK_TARIFF)
        current = (
            _network_form_values(previous if isinstance(previous, dict) else {})
            if user_input is None else user_input
        )
        if user_input is None:
            current[CONF_AUTO_TARIFF_REFRESH] = self.config_entry.options.get(
                CONF_AUTO_TARIFF_REFRESH, False,
            )
        if user_input is None and module == NETWORK_MODULE_3:
            if not self._home_tariff_lookup_done:
                self._home_tariff_values = await async_lookup_home_tariff(self.hass)
                self._home_tariff_lookup_done = True
            if self._home_tariff_values is not None:
                status = self._home_tariff_values.get("_source_status")
                prefix = "⚠ " if status == "vorlaeufig" else ""
                self._home_tariff_source = prefix + str(self._home_tariff_values.get("_source_url") or "—")
                current.update({
                    key: value for key, value in self._home_tariff_values.items()
                    if not key.startswith("_source_")
                })
                if not (isinstance(previous, dict) and previous.get("module") == NETWORK_MODULE_3):
                    current[CONF_AUTO_TARIFF_REFRESH] = True
        return self.async_show_form(
            step_id="cost_network",
            data_schema=cost_network_schema(current, module=module),
            description_placeholders={
                "module": "2" if module != NETWORK_MODULE_3 else "3",
                "source": self._home_tariff_source,
            },
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
    for key in (
        CONF_VEHICLE_SOC_ENTITY, CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
        CONF_TIMED_CAPACITY_ENTITY, CONF_TIMED_DEPARTURE_ENTITY,
        CONF_TIMED_CALENDAR_ENTITY, CONF_TIMED_CALENDAR_TITLE,
        CONF_TIMED_BAVARIAN_DEVICE, CONF_TIMED_VEHICLE_TIME_ZONE,
        CONF_TIMED_TIMER1_DAYS, CONF_TIMED_TIMER2_DAYS,
    ):
        if value := user_input.get(key):
            options[key] = value
    if (value := user_input.get(CONF_TIMED_CAPACITY_KWH)) is not None:
        options[CONF_TIMED_CAPACITY_KWH] = float(value)
    return options


def _timed_errors(values: dict[str, Any]) -> dict[str, str]:
    """Reject ambiguous sources and an unbounded/unknown battery capacity."""
    errors: dict[str, str] = {}
    for key, domain in (
        (CONF_TIMED_CAPACITY_ENTITY, ("sensor",)),
        (CONF_TIMED_DEPARTURE_ENTITY, ("sensor", "input_datetime")),
        (CONF_TIMED_CALENDAR_ENTITY, ("calendar",)),
    ):
        value = values.get(key)
        if value and (not isinstance(value, str) or not valid_entity_id(value)
                      or value.split(".", 1)[0] not in domain):
            errors[key] = "timed_source_invalid"
    if values.get(CONF_TIMED_CAPACITY_ENTITY) and values.get(CONF_TIMED_CAPACITY_KWH) is not None:
        errors[CONF_TIMED_CAPACITY_ENTITY] = "timed_capacity_ambiguous"
    if values.get(CONF_TIMED_CALENDAR_TITLE) and not values.get(CONF_TIMED_CALENDAR_ENTITY):
        errors[CONF_TIMED_CALENDAR_TITLE] = "timed_calendar_required"
    if values.get(CONF_TIMED_CALENDAR_ENTITY) and not str(values.get(CONF_TIMED_CALENDAR_TITLE, "")).strip():
        errors[CONF_TIMED_CALENDAR_TITLE] = "timed_calendar_title_required"
    if any(values.get(key) for key in (
        CONF_TIMED_DEPARTURE_ENTITY, CONF_TIMED_CALENDAR_ENTITY,
        CONF_TIMED_CAPACITY_ENTITY, CONF_TIMED_CAPACITY_KWH, CONF_TIMED_BAVARIAN_DEVICE,
    )) and not values.get(CONF_VEHICLE_SOC_ENTITY):
        errors[CONF_VEHICLE_SOC_ENTITY] = "vehicle_soc_required"
    for key in (CONF_TIMED_TIMER1_DAYS, CONF_TIMED_TIMER2_DAYS):
        days = values.get(key, [])
        if not isinstance(days, list) or any(day not in TIMED_WEEKDAYS for day in days):
            errors[key] = "timed_weekdays_invalid"
    zone = values.get(CONF_TIMED_VEHICLE_TIME_ZONE)
    if zone:
        try:
            ZoneInfo(zone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            errors[CONF_TIMED_VEHICLE_TIME_ZONE] = "timed_timezone_invalid"
    if any(values.get(key) for key in (
        CONF_TIMED_TIMER1_DAYS, CONF_TIMED_TIMER2_DAYS, CONF_TIMED_VEHICLE_TIME_ZONE,
    )) and not values.get(CONF_TIMED_BAVARIAN_DEVICE):
        errors[CONF_TIMED_BAVARIAN_DEVICE] = "timed_bavarian_required"
    capacity = values.get(CONF_TIMED_CAPACITY_KWH)
    if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, (int, float))
                                 or not math.isfinite(capacity) or not 1 <= capacity <= 250):
        errors[CONF_TIMED_CAPACITY_KWH] = "timed_capacity_invalid"
    return errors


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


def _cost_errors(
    source: str, values: dict[str, Any], *, allow_network: bool = True,
) -> dict[str, str]:
    errors = {}
    if source == COST_SOURCE_FIXED:
        value = values.get(CONF_CHARGING_PRICE)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > 1000 or not math.isfinite(value):
            errors[CONF_CHARGING_PRICE] = "cost_price_invalid"
    else:
        value = values.get(CONF_CHARGING_PRICE_ENTITY)
        if not isinstance(value, str) or not valid_entity_id(value) or not value.startswith("sensor."):
            errors[CONF_CHARGING_PRICE_ENTITY] = "cost_source_invalid"
    module = values.get(CONF_CHARGING_NETWORK_MODULE, NETWORK_MODULE_NONE)
    if module not in NETWORK_MODULES or (not allow_network and module != NETWORK_MODULE_NONE):
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
    parsed = TariffConfig.parse(tariff)
    if any(window.start % 15 or window.end % 15 for window in parsed.windows):
        raise ValueError("module 3 windows require quarter-hour boundaries")
    return parsed.as_dict()


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
