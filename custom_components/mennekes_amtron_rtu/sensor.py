"""Sensor platform for the MENNEKES AMTRON integration.

Every numeric state is rounded to the precision the entity declares it will
display. Home Assistant stores the native value, not the displayed one, so a
sensor that reports more digits than it is willing to show pays for them in
the recorder on every poll -- and a float32 from the device changes its last
bits whenever the meter recomputes, which turns a steady mains voltage into a
database row every few seconds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .charge_loss_const import LOSS_SENSOR_KEYS
from .coordinator import MennekesAmtronCoordinator
from .device.capabilities import capability_is_supported
from .device.charging_status import charging_status
from .device.decode import RegisterValue, enum_option
from .device.registers import CP_STATE, ENERGY_TOTAL, EVSE_STATE, REGISTERS_BY_KEY
from .entity import MennekesAmtronEntity
from .entity_attributes import read_attributes
from .entry_types import MennekesAmtronConfigEntry
from .estimate_sensor import AmtronEstimateSensor
from .sensor_descriptions import (
    CONTROL_SENSOR_DESCRIPTIONS,
    ESTIMATE_SENSOR_DESCRIPTIONS,
    OPERATION_SENSOR_KEYS,
    ORDER_SENSOR_KEYS,
    SENSOR_DESCRIPTIONS,
    AmtronSensorEntityDescription,
)
from .vehicle_features import LOSS, SOC, TIMED, vehicle_features

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the sensors this device supports."""

    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    features = vehicle_features(hass, entry)
    entities: list[SensorEntity] = []
    for description in SENSOR_DESCRIPTIONS:
        if not capability_is_supported(
            identity.layout_version,
            identity.phase_options_hw,
            description.register_key,
        ):
            continue
        entities.append(AmtronSensor(entry, runtime_data.coordinator, description))
    entities.extend(
        AmtronStateSensor(entry, runtime_data.coordinator, description)
        for description in CONTROL_SENSOR_DESCRIPTIONS
        if (description.key != "energy_goal_status" or identity.layout_version >= ENERGY_TOTAL.min_layout)
        and (description.key != "soc_goal_status" or SOC in features)
        and (description.key != "timed_goal_status" or TIMED in features)
        and (description.key != "virtual_plug_charge_status" or runtime_data.plug_charge is not None)
        and (description.key not in OPERATION_SENSOR_KEYS or runtime_data.control.operation is not None)
        and (description.key not in ORDER_SENSOR_KEYS or runtime_data.orders is not None)
    )
    entities.extend(
        AmtronEstimateSensor(entry, runtime_data.coordinator, description)
        for description in ESTIMATE_SENSOR_DESCRIPTIONS
        if (LOSS in features if description.key in LOSS_SENSOR_KEYS else runtime_data.charging_cost is not None)
    )
    async_add_entities(entities)


def _tenths(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 1)


def _moment(value: object) -> datetime | None:
    """A time of the app as a time attribute; anything else is left out."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class AmtronStateSensor(MennekesAmtronEntity, SensorEntity):
    """Expose derived status and fixed queue metadata without ticking counters."""

    def __init__(
        self,
        entry: MennekesAmtronConfigEntry,
        coordinator: MennekesAmtronCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(entry, coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Heartbeat health changes between polls and even when polling stopped.
        heartbeat = self.runtime_data.heartbeat
        if self.entity_description.key == "connection_quality" and heartbeat is not None:
            self.async_on_remove(heartbeat.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        if self.entity_description.key == "energy_goal_status":
            return self.runtime_data.control.supports_energy_goal
        if self.entity_description.key == "soc_goal_status":
            observer = self.runtime_data.vehicle_soc
            return (
                self.runtime_data.control.supports_soc_goal
                and observer is not None and observer.source is not None
            )
        if self.entity_description.key == "timed_goal_status":
            return self.runtime_data.timed_goal is not None
        if self.entity_description.key in OPERATION_SENSOR_KEYS:
            return self.runtime_data.control.operation is not None
        if self.entity_description.key in ORDER_SENSOR_KEYS:
            return self._orders is not None
        return True

    @property
    def _orders(self) -> dict[str, Any] | None:
        """The app's status behind an order sensor; None while it is unknown."""
        orders = self.runtime_data.orders
        if orders is None:
            return None
        return orders.jobs

    @property
    def native_value(self) -> str | None:
        key = self.entity_description.key
        if key == "charging_status":
            return charging_status(
                self.wallbox_data,
                cp_supported=self.runtime_data.identity.layout_version >= CP_STATE.min_layout,
            )
        if key == "connection_quality":
            return self.wallbox_data.connection_quality
        if key in ORDER_SENSOR_KEYS:
            status = self._orders
            if status is None:
                return None
            job = status.get("job")
            state = job.get("state") if isinstance(job, dict) else "none"
            return state if state in (self.options or ()) else None
        control = self.runtime_data.control
        if key in OPERATION_SENSOR_KEYS:
            operation = control.operation
            if operation is None:
                return None
            return {
                "operation_controller": operation.controller,
                "operation_waiting": operation.waiting,
            }.get(key, operation.next_event)
        if key == "energy_goal_status":
            return control.energy_goal.status
        if key == "soc_goal_status":
            return control.soc_goal.status
        if key == "timed_goal_status":
            observer = self.runtime_data.timed_goal
            return observer.status if observer is not None else None
        if key == "virtual_plug_charge_status":
            plug_charge = self.runtime_data.plug_charge
            return plug_charge.status if plug_charge is not None else None
        return control.pending_action or "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The details behind a status: fixed values, never ticking counters."""
        key = self.entity_description.key
        attributes: dict[str, Any] = {}
        if key == "charging_status":
            data = self.wallbox_data
            attributes = {
                "evse_state": enum_option(EVSE_STATE, data.get(EVSE_STATE.key)),
                "cp_state": enum_option(CP_STATE, data.get(CP_STATE.key)),
            }
        elif key == "connection_quality":
            heartbeat = self.runtime_data.heartbeat
            healthy = heartbeat.healthy if heartbeat is not None else None
            attributes = {
                "heartbeat": None if healthy is None else "ok" if healthy else "problem",
                "failed_transactions": self.wallbox_data.connection_failures,
                "retried_transactions": self.wallbox_data.connection_retries,
            }
        elif key == "pending_change":
            attributes = {"due": self.runtime_data.control.pending_until}
        elif key == "timed_goal_status":
            observer = self.runtime_data.timed_goal
            forecast = observer.forecast if observer is not None else None
            attributes = {
                "planned_start": observer.planned_start if observer is not None else None,
                "estimated_finish": forecast.finish_at if forecast is not None else None,
                "estimated_level_at_departure": forecast.level_at_departure if forecast is not None else None,
                "learned_efficiency": forecast.efficiency if forecast is not None else None,
                "measured_power_kw": forecast.full_power_kw if forecast is not None else None,
            }
        elif key == "charging_job" and (status := self._orders) is not None:
            job = status.get("job")
            attributes = {"boost_available": status.get("boost_available")}
            if isinstance(job, dict):
                attributes |= {
                    "kind": job.get("kind", "scheduled"),
                    "start": _moment(job.get("start_at")),
                    "finish": _moment(job.get("finish_at")),
                    "current": job.get("current_a"),
                    "energy": job.get("energy_kwh"),
                    # Whole tenths: a running charge must not write a state per poll.
                    "delivered": _tenths(job.get("delivered_kwh")),
                    "reserve": job.get("reserve_kwh"),
                    "reason": job.get("reason"),
                    "boost_until": _moment(job.get("boost_until")),
                }
        elif key == "operation_controller" and (operation := self.runtime_data.control.operation) is not None:
            attributes = {
                "vehicle": operation.vehicle,
                "target": None if operation.target_kind == "none" else operation.target_kind,
                "target_value": operation.target_value,
                "target_deadline": operation.target_deadline,
            }
        elif key == "operation_next" and (operation := self.runtime_data.control.operation) is not None:
            attributes = {"at": operation.next_at}
        elif key == "virtual_plug_charge_status":
            plug_charge = self.runtime_data.plug_charge
            attributes = {
                "blocked_reason": plug_charge.blocked_reason if plug_charge is not None else None,
                "decision_reason": getattr(plug_charge, "decision_reason", None),
            }
        return {name: value for name, value in attributes.items() if value is not None} or None


class AmtronSensor(MennekesAmtronEntity, SensorEntity):
    """A sensor reading one register."""

    entity_description: AmtronSensorEntityDescription

    def __init__(
        self,
        entry: MennekesAmtronConfigEntry,
        coordinator: MennekesAmtronCoordinator,
        description: AmtronSensorEntityDescription,
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            description.key,
            register_key=description.register_key,
        )
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the decoded register value as a Home Assistant state."""

        value = self.register_value
        description = self.entity_description
        if description.value_fn is not None:
            return _rounded(description.value_fn(value), description)
        if description.enum_class is not None:
            return _enum_state(description, value)
        if isinstance(value, (int, float, str)):
            return _rounded(value, description)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The registers that detail this sensor's state."""

        return read_attributes(self.wallbox_data, self.entity_description.attributes)


def _rounded(
    value: StateType, description: AmtronSensorEntityDescription
) -> StateType:
    """Return a float trimmed to the precision this sensor displays.

    ``suggested_display_precision`` only tells the frontend how to render the
    state; the value Home Assistant records and builds statistics from is the
    one returned here. Decoding a float32 into a Python float also carries the
    conversion artefact along -- 230.1 V arrives as 230.10000610351562 -- so
    this is what keeps both the database and the history readable.
    """

    precision = description.suggested_display_precision
    if precision is None or not isinstance(value, float):
        return value
    return round(value, precision)


def _enum_state(
    description: AmtronSensorEntityDescription, value: RegisterValue
) -> StateType:
    """Return the state option of an enum register value.

    The register map owns the mapping, so an undocumented value becomes no
    state at all instead of an option Home Assistant does not know.
    """

    spec = REGISTERS_BY_KEY.get(description.register_key)
    return enum_option(spec, value) if spec is not None else None
