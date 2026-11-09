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

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .charge_loss_const import LOSS_SENSOR_KEYS
from .coordinator import MennekesAmtronCoordinator
from .device.capabilities import capability_is_supported
from .device.charging_status import charging_status
from .device.decode import RegisterValue, enum_option
from .device.registers import CP_STATE, ENERGY_TOTAL, REGISTERS_BY_KEY
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .estimate_sensor import AmtronEstimateSensor
from .sensor_descriptions import (
    CONTROL_SENSOR_DESCRIPTIONS,
    ESTIMATE_SENSOR_DESCRIPTIONS,
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
        if description.key == "error_code":
            entities.append(
                AmtronErrorCodeSensor(entry, runtime_data.coordinator, description)
            )
        else:
            entities.append(
                AmtronSensor(entry, runtime_data.coordinator, description)
            )
    entities.extend(
        AmtronStateSensor(entry, runtime_data.coordinator, description)
        for description in CONTROL_SENSOR_DESCRIPTIONS
        if (description.key != "energy_goal_status" or identity.layout_version >= ENERGY_TOTAL.min_layout)
        and (description.key != "soc_goal_status" or SOC in features)
        and (description.key not in ("timed_goal_status", "timed_goal_planned_start") or TIMED in features)
        and (description.key != "virtual_plug_charge_status" or runtime_data.plug_charge is not None)
    )
    entities.extend(
        AmtronEstimateSensor(entry, runtime_data.coordinator, description)
        for description in ESTIMATE_SENSOR_DESCRIPTIONS
        if (LOSS in features if description.key in LOSS_SENSOR_KEYS else runtime_data.charging_cost is not None)
    )
    async_add_entities(entities)


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
        if self.entity_description.key in ("timed_goal_status", "timed_goal_planned_start"):
            return self.runtime_data.timed_goal is not None
        return True

    @property
    def native_value(self) -> str | datetime | None:
        key = self.entity_description.key
        if key == "charging_status":
            return charging_status(
                self.wallbox_data,
                cp_supported=self.runtime_data.identity.layout_version >= CP_STATE.min_layout,
            )
        if key == "connection_quality":
            return self.wallbox_data.connection_quality
        control = self.runtime_data.control
        if key == "energy_goal_status":
            return control.energy_goal.status
        if key == "soc_goal_status":
            return control.soc_goal.status
        if key == "timed_goal_status":
            observer = self.runtime_data.timed_goal
            return observer.status if observer is not None else None
        if key == "timed_goal_planned_start":
            observer = self.runtime_data.timed_goal
            return observer.planned_start if observer is not None else None
        if key == "virtual_plug_charge_status":
            plug_charge = self.runtime_data.plug_charge
            return plug_charge.status if plug_charge is not None else None
        if key == "pending_change":
            return control.pending_action or "none"
        return control.pending_until

    @property
    def extra_state_attributes(self) -> dict[str, str | int] | None:
        """Explain a blocked virtual release without exposing session data."""
        if self.entity_description.key == "connection_quality":
            counts = {
                "failed_transactions": self.wallbox_data.connection_failures,
                "retried_transactions": self.wallbox_data.connection_retries,
            }
            return {key: value for key, value in counts.items() if value is not None} or None
        if self.entity_description.key != "virtual_plug_charge_status":
            return None
        plug_charge = self.runtime_data.plug_charge
        reason = plug_charge.blocked_reason if plug_charge is not None else None
        return {"blocked_reason": reason} if reason is not None else None


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


class AmtronErrorCodeSensor(AmtronSensor):
    """The active error code, reported as a documented category.

    The register is a plain number and the specification documents only a few
    of them, so the raw code stays visible as an attribute instead of being
    squeezed into an invented option name.
    """

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Return the raw error code of the wallbox."""

        value = self.register_value
        if not isinstance(value, int):
            return None
        return {"code": value}


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
