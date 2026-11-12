"""Static sensor descriptions, derived from the register map.

Each sensor answers one question. A register that only details or explains
another sensor's state is one of its attributes: the phase voltages ride on
the total power, the session counters on the session energy, the installation
settings on the wallbox settings sensor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from homeassistant.components.sensor import (
    SensorDeviceClass,
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

from .device.charging_status import CHARGING_STATUS_OPTIONS
from .device.control import PENDING_ACTIONS
from .device.decode import RegisterValue
from .device.energy_goal import ENERGY_GOAL_STATES
from .device.enums import (
    AuthorizationStatus,
    CableLockStatus,
    DetectedEvPhases,
    enum_options,
)
from .device.registers import layout_label
from .device.soc_goal import SOC_GOAL_STATES
from .entity_attributes import (
    AttributeReaders,
    enum_attribute,
    flag_attribute,
    integer_attribute,
    minutes_attribute,
    number_attribute,
    under_load,
)

CONTROL_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="virtual_plug_charge_status",
        translation_key="virtual_plug_charge_status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "disabled", "waiting_vehicle", "waiting_detection", "vehicle_not_selected",
            "waiting_release", "authorized", "manual_release", "blocked",
        ],
    ),
    SensorEntityDescription(
        key="timed_goal_status",
        translation_key="timed_goal_status",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "disabled", "waiting_for_target", "waiting_for_capacity",
            "waiting_for_soc", "waiting_for_device", "vehicle_unconfirmed", "read_only", "queued",
            "paused_until_start", "charging", "finishing", "unreachable",
            "late", "reached", "write_pending", "error",
        ],
    ),
    SensorEntityDescription(
        key="soc_goal_status",
        translation_key="soc_goal_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(SOC_GOAL_STATES),
    ),
    SensorEntityDescription(
        key="energy_goal_status",
        translation_key="energy_goal_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(ENERGY_GOAL_STATES),
    ),
    SensorEntityDescription(
        key="charging_status",
        translation_key="charging_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(CHARGING_STATUS_OPTIONS),
    ),
    SensorEntityDescription(
        key="connection_quality",
        translation_key="connection_quality",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=["good", "degraded"],
    ),
    SensorEntityDescription(
        key="pending_change",
        translation_key="pending_change",
        device_class=SensorDeviceClass.ENUM,
        options=["none", *PENDING_ACTIONS],
    ),
)


@dataclass(frozen=True, kw_only=True)
class AmtronSensorEntityDescription(SensorEntityDescription):
    """Describe one sensor backed by a register, with the registers it details."""

    register_key: str
    enum_class: type[IntEnum] | None = None
    value_fn: Callable[[RegisterValue], str | float | None] | None = None
    attributes: AttributeReaders = field(default_factory=dict)


def _enum(
    key: str,
    enum_class: type[IntEnum],
    *,
    entity_category: EntityCategory | None = None,
    attributes: AttributeReaders | None = None,
) -> AmtronSensorEntityDescription:
    return AmtronSensorEntityDescription(
        key=key,
        translation_key=key,
        register_key=key,
        device_class=SensorDeviceClass.ENUM,
        options=list(enum_options(enum_class)),
        enum_class=enum_class,
        entity_category=entity_category,
        attributes=attributes or {},
    )


def _current(key: str) -> AmtronSensorEntityDescription:
    return AmtronSensorEntityDescription(
        key=key,
        translation_key=key,
        register_key=key,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    )


SENSOR_DESCRIPTIONS: tuple[AmtronSensorEntityDescription, ...] = (
    _enum(
        "authorization_status", AuthorizationStatus,
        attributes={"enabled": flag_attribute("authorization_enabled")},
    ),
    _enum("detected_ev_phases", DetectedEvPhases),
    _current("signaled_current"),
    AmtronSensorEntityDescription(
        key="power_total",
        translation_key="power_total",
        register_key="power_total",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        # The phase details under load, in whole volts and watts and tenths
        # of an ampere: while charging, the power changes with every poll
        # anyway, so they add no history rows.
        attributes={
            **{f"current_l{n}": under_load(number_attribute(f"current_l{n}", 1)) for n in (1, 2, 3)},
            **{f"voltage_l{n}": under_load(number_attribute(f"voltage_l{n}", 0)) for n in (1, 2, 3)},
            **{f"power_l{n}": under_load(number_attribute(f"power_l{n}", 0)) for n in (1, 2, 3)},
        },
    ),
    AmtronSensorEntityDescription(
        key="session_energy",
        translation_key="session_energy",
        register_key="session_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        attributes={
            "duration_minutes": minutes_attribute("session_duration"),
            "maximum_current": number_attribute("session_max_current", 2),
            "sessions": integer_attribute("sessions_total"),
        },
    ),
    AmtronSensorEntityDescription(
        key="energy_total",
        translation_key="energy_total",
        register_key="energy_total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    # Diagnostic category
    AmtronSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        register_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _enum(
        "cable_lock_status", CableLockStatus,
        entity_category=EntityCategory.DIAGNOSTIC,
        attributes={"permanent": flag_attribute("cable_lock_setting")},
    ),
    # The installation: set by DIP switches and the installer, read once per
    # poll but changing only when someone opens the wallbox.
    AmtronSensorEntityDescription(
        key="wallbox_settings",
        translation_key="wallbox_settings",
        register_key="modbus_layout_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda value: layout_label(value) if isinstance(value, int) else None,
        attributes={
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
        },
    ),
)

# Optional estimates have no state class: a per-charge value is neither a
# monotonic counter nor an instantaneous measurement for long-term statistics.
ESTIMATE_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="last_charging_loss", translation_key="last_charging_loss",
        native_unit_of_measurement="%", suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="average_charging_loss", translation_key="average_charging_loss",
        native_unit_of_measurement="%", suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="current_estimated_charging_cost", translation_key="current_estimated_charging_cost",
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR",
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="last_estimated_charging_cost", translation_key="last_estimated_charging_cost",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR",
        suggested_display_precision=2,
    ),
)
