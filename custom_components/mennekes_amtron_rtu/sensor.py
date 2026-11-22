"""Sensors: what the wallbox measures, what it is doing, and why it waits.

Every numeric state is rounded to the precision the entity declares it will
display. Home Assistant stores the native value, not the displayed one, and a
float32 from the device changes its last bits whenever the meter recomputes:
unrounded, a steady reading becomes a database row every few seconds.

Vocabulary of every status here: ``disabled`` means switched off by a
setting, ``none`` means nothing applies right now.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .device.charging_display import DISPLAY_OPTIONS
from .device.charging_status import charging_status
from .device.decode import enum_option
from .device.enums import DowngradeStatus
from .device.link_quality import CONNECTION_QUALITY_STATES
from .device.operation import WAITING
from .device.registers import CP_STATE, EVSE_STATE, REGISTERS_BY_KEY, layout_label
from .entity import AmtronEntity, Projection, Runtime, wanted
from .entity_attributes import (
    AttributeReader,
    enum_attribute,
    flag_attribute,
    integer_attribute,
    minutes_attribute,
    number_attribute,
    under_load,
)
from .entry_types import MennekesAmtronConfigEntry
from .vehicle_features import LOSS

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AmtronSensorDescription(Projection, SensorEntityDescription):
    """One sensor: its state and what it needs to exist."""

    value: Callable[[Runtime], StateType]
    # The connection quality follows the heartbeat between polls as well.
    follows_heartbeat: bool = False


def _registers(readers: dict[str, AttributeReader]) -> Callable[[Runtime], dict[str, Any]]:
    """Attributes read from other registers of the same poll."""
    return lambda runtime: {
        name: reader(runtime.coordinator.current_data()) for name, reader in readers.items()
    }


def _register(key: str) -> Callable[[Runtime], StateType]:
    def read(runtime: Runtime) -> StateType:
        value = runtime.coordinator.current_data().get(key)
        return value if isinstance(value, (int, float, str)) else None

    return read


def _energy_total(runtime: Runtime) -> StateType:
    value = runtime.coordinator.current_data().get("energy_total")
    return value if isinstance(value, (int, float)) else runtime.client.last_energy_total


def _enum_register(key: str) -> Callable[[Runtime], StateType]:
    # The register map owns the mapping: an undocumented value becomes no
    # state at all instead of an option Home Assistant does not know.
    spec = REGISTERS_BY_KEY[key]
    return lambda runtime: enum_option(spec, runtime.coordinator.current_data().get(key))


def _enum(key: str, *, attributes: dict[str, AttributeReader] | None = None) -> AmtronSensorDescription:
    """A diagnostic register with named values, hidden until someone wants it."""
    options = REGISTERS_BY_KEY[key].enum_map
    if options is None:
        raise ValueError(f"{key} has no named values")
    return AmtronSensorDescription(
        key=key, register=key, value=_enum_register(key),
        device_class=SensorDeviceClass.ENUM, options=list(dict.fromkeys(options.values())),
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        attributes=_registers(attributes) if attributes else None,
    )


def _charging_status(runtime: Runtime) -> StateType:
    data = runtime.coordinator.current_data()
    return data.charging_display or charging_status(
        data, cp_supported=runtime.identity.layout_version >= CP_STATE.min_layout,
    )


def _charging_details(runtime: Runtime) -> dict[str, Any]:
    data = runtime.coordinator.current_data()
    downgraded = data.get("downgrade_status") == DowngradeStatus.DOWNGRADED
    return {
        "evse_state": enum_option(EVSE_STATE, data.get(EVSE_STATE.key)),
        "cp_state": enum_option(CP_STATE, data.get(CP_STATE.key)),
        "downgraded_to": number_attribute("downgrade_current", 2)(data) if downgraded else None,
    }


def _waiting(runtime: Runtime) -> StateType:
    operation = runtime.control.operation
    return operation.waiting if operation is not None else None


def _waiting_details(runtime: Runtime) -> dict[str, Any]:
    """Who controls the charge, what comes next and what it aims for.

    Fixed values only, never a ticking counter: an attribute that changed on
    every poll would write a history row each time.
    """
    control = runtime.control
    operation = control.operation
    if operation is None:
        return {}
    details: dict[str, Any] = {
        "controller": operation.controller,
        "next_event": operation.next_event,
        "next_at": operation.next_at,
        "vehicle": operation.vehicle,
        "target": None if operation.target_kind == "none" else operation.target_kind,
        "target_value": operation.target_value,
        "target_deadline": operation.target_deadline,
        "pending": control.pending_action or "none",
        "pending_at": control.pending_until,
        "energy_target": control.energy_goal.status if control.supports_energy_goal else None,
        "cost_target": None if runtime.cost_goal is None else runtime.cost_goal.status,
        "vehicle_target": (
            control.soc_goal.status
            if runtime.vehicle_soc is not None and runtime.vehicle_soc.source is not None else None
        ),
    }
    if (plan := runtime.departure_observer) is not None:
        forecast = plan.forecast
        details |= {
            "departure_plan": plan.status,
            "planned_start": plan.planned_start,
            "estimated_finish": forecast.finish_at if forecast is not None else None,
            "estimated_level_at_departure": forecast.level_at_departure if forecast is not None else None,
        }
    if (gate := runtime.plug_charge) is not None:
        details |= {"plug_charge": gate.status, "plug_charge_blocked": gate.blocked_reason}
    return details


def _connection_details(runtime: Runtime) -> dict[str, Any]:
    healthy = runtime.heartbeat.healthy if runtime.heartbeat is not None else None
    return {"heartbeat": None if healthy is None else "ok" if healthy else "problem"}


def _loss(last: bool) -> Callable[[Runtime], StateType]:
    def read(runtime: Runtime) -> StateType:
        if runtime.charge_loss is None:
            return None
        statistics = runtime.charge_loss.statistics
        return statistics.last_loss_percent if last else statistics.average_loss_percent

    return read


def _loss_details(last: bool) -> Callable[[Runtime], dict[str, Any]]:
    def read(runtime: Runtime) -> dict[str, Any]:
        if runtime.charge_loss is None:
            return {}
        statistics = runtime.charge_loss.statistics
        return {
            "sample_count": statistics.sample_count,
            "association": statistics.association if last else None,
            # Why the last charge has a loss or has none.
            "status": statistics.status if last else None,
        }

    return read


def _cost(current: bool) -> Callable[[Runtime], StateType]:
    def read(runtime: Runtime) -> StateType:
        cost = runtime.charging_cost
        if cost is None:
            return None
        result = cost.current if current else cost.last.result
        # An estimate shows what it knows; ``complete`` tells the rest.
        return result.estimate if result is not None else None

    return read


def _cost_details(current: bool) -> Callable[[Runtime], dict[str, Any]]:
    def read(runtime: Runtime) -> dict[str, Any]:
        cost = runtime.charging_cost
        result = None if cost is None else cost.current if current else cost.last.result
        if result is None:
            return {}
        if current:
            return {"complete": result.complete}
        return {
            "complete": result.complete,
            "energy_kwh": round(result.energy_kwh, 2),
            "unpriced_kwh": round(result.unpriced_kwh, 2),
        }

    return read


def _loss_ready(runtime: Runtime) -> bool:
    return runtime.charge_loss is not None and runtime.charge_loss.available


def _cost_ready(runtime: Runtime) -> bool:
    return runtime.charging_cost is not None and runtime.charging_cost.available


SENSORS: tuple[AmtronSensorDescription, ...] = (
    # --- What the wallbox measures.
    AmtronSensorDescription(
        key="power_total", register="power_total", value=_register("power_total"),
        native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=0,
        # The phase details under load, in whole volts and watts and tenths
        # of an ampere. They are shown live and not recorded.
        attributes=_registers({
            **{f"current_l{n}": under_load(number_attribute(f"current_l{n}", 1)) for n in (1, 2, 3)},
            **{f"voltage_l{n}": under_load(number_attribute(f"voltage_l{n}", 0)) for n in (1, 2, 3)},
            **{f"power_l{n}": under_load(number_attribute(f"power_l{n}", 0)) for n in (1, 2, 3)},
        }),
    ),
    AmtronSensorDescription(
        # One failed meter read is no gap in the energy statistics.
        key="energy_total", capability="energy_total", value=_energy_total,
        ready=lambda runtime: _energy_total(runtime) is not None,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=2,
    ),
    AmtronSensorDescription(
        key="session_energy", register="session_energy", value=_register("session_energy"),
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=3,
        attributes=_registers({
            "duration_minutes": minutes_attribute("session_duration"),
            "maximum_current": number_attribute("session_max_current", 2),
            "sessions": integer_attribute("sessions_total"),
        }),
    ),
    AmtronSensorDescription(
        key="signaled_current", register="signaled_current", value=_register("signaled_current"),
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2,
    ),
    AmtronSensorDescription(
        key="temperature", register="temperature", value=_register("temperature"),
        native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- What it is doing, and why it waits.
    AmtronSensorDescription(
        key="charging_status", value=_charging_status,
        device_class=SensorDeviceClass.ENUM, options=list(DISPLAY_OPTIONS),
        attributes=_charging_details,
    ),
    AmtronSensorDescription(
        key="operation_waiting", value=_waiting,
        device_class=SensorDeviceClass.ENUM, options=list(WAITING),
        # Shown from the service's last answer: "wallbox unavailable" is one
        # of its reasons.
        offline=True,
        ready=lambda runtime: runtime.client.connected and runtime.control.operation is not None,
        attributes=_waiting_details,
    ),
    # --- Estimates. No state class: a per-charge value is neither a counter
    # nor an instantaneous measurement for long-term statistics.
    AmtronSensorDescription(
        key="last_charging_loss", value=_loss(last=True), feature=lambda features: LOSS in features,
        native_unit_of_measurement="%", suggested_display_precision=1,
        # Kept values of finished charges: shown whatever the wallbox does.
        offline=True, ready=_loss_ready, attributes=_loss_details(last=True),
    ),
    AmtronSensorDescription(
        key="average_charging_loss", value=_loss(last=False), feature=lambda features: LOSS in features,
        native_unit_of_measurement="%", suggested_display_precision=1,
        offline=True, ready=_loss_ready, attributes=_loss_details(last=False),
    ),
    AmtronSensorDescription(
        key="current_estimated_charging_cost", value=_cost(current=True),
        exists=lambda runtime: runtime.charging_cost is not None,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2, entity_registry_enabled_default=False,
        offline=True, ready=_cost_ready, attributes=_cost_details(current=True),
    ),
    AmtronSensorDescription(
        key="last_estimated_charging_cost", value=_cost(current=False),
        exists=lambda runtime: runtime.charging_cost is not None,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        offline=True, ready=_cost_ready, attributes=_cost_details(current=False),
    ),
    # --- Diagnostics, off until someone needs them.
    AmtronSensorDescription(
        key="connection_quality",
        value=lambda runtime: runtime.coordinator.current_data().connection_quality,
        device_class=SensorDeviceClass.ENUM, options=list(CONNECTION_QUALITY_STATES),
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        attributes=_connection_details, follows_heartbeat=True,
    ),
    _enum("authorization_status", attributes={"enabled": flag_attribute("authorization_enabled")}),
    _enum("cable_lock_status", attributes={"permanent": flag_attribute("cable_lock_setting")}),
    _enum("detected_ev_phases"),
    # The installation: set by DIP switches and the installer, changing only
    # when someone opens the wallbox.
    AmtronSensorDescription(
        key="wallbox_settings", register="modbus_layout_version",
        value=lambda runtime: (
            layout_label(value)
            if isinstance(value := runtime.coordinator.current_data().get("modbus_layout_version"), int)
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        attributes=_registers({
            "max_current_house": number_attribute("max_current_house", 2),
            "max_evse_current": number_attribute("max_evse_current", 2),
            "phase_rotation": enum_attribute("phase_rotation"),
            "grid_phases_connected": enum_attribute("grid_phases_connected"),
            "phase_options": enum_attribute("phase_options_hw"),
            "phase_switching_mode": enum_attribute("phase_switching_mode"),
            "phase_switching_pause_seconds": integer_attribute("phase_switching_pause"),
            "grid_imbalance_monitoring": flag_attribute("grid_imbalance"),
            "grid_imbalance_threshold": number_attribute("grid_imbalance_threshold", 2),
            "solar_min_current": number_attribute("solar_min_current", 2),
        }),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(AmtronSensor(entry, description) for description in wanted(entry, SENSORS))


class AmtronSensor(AmtronEntity, SensorEntity):
    """A sensor described by one row of the table."""

    entity_description: AmtronSensorDescription
    # Live details that change with nearly every reading: recorded, each
    # reading would store its own copy of all attributes.
    _unrecorded_attributes = frozenset({
        *(f"{kind}_l{n}" for kind in ("current", "voltage", "power") for n in (1, 2, 3)),
        "duration_minutes",
    })

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        heartbeat = self.runtime.heartbeat
        if self.entity_description.follows_heartbeat and heartbeat is not None:
            self.async_on_remove(heartbeat.async_add_listener(self.async_write_ha_state))

    @property
    def native_unit_of_measurement(self) -> str | None:
        # Money is in Home Assistant's currency, as the price it reports.
        if self.entity_description.device_class is SensorDeviceClass.MONETARY:
            return self.hass.config.currency
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> StateType:
        value = self.entity_description.value(self.runtime)
        precision = self.entity_description.suggested_display_precision
        if precision is None or not isinstance(value, float):
            return value
        return round(value, precision)
