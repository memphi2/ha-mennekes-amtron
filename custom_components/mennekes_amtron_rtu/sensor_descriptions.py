"""Static sensor descriptions, derived from the register map."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)

from .device.charging_status import CHARGING_STATUS_OPTIONS
from .device.control import PENDING_ACTIONS
from .device.decode import RegisterValue
from .device.energy_goal import ENERGY_GOAL_STATES
from .device.enums import (
    AuthorizationStatus,
    CableLockStatus,
    CpState,
    DetectedEvPhases,
    EmsFallbackBehaviour,
    ErrorCategory,
    EvseState,
    GridPhasesConnected,
    PhaseRotation,
    PhaseSwitchingMode,
    SwitchedPhases,
    ems_fallback_behaviour,
    ems_fallback_current,
    enum_option,
    enum_options,
    error_category,
)
from .device.registers import layout_label
from .device.soc_goal import SOC_GOAL_STATES

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
        key="timed_goal_planned_start",
        translation_key="timed_goal_planned_start",
        device_class=SensorDeviceClass.TIMESTAMP,
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
    SensorEntityDescription(
        key="pending_change_until",
        translation_key="pending_change_until",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


@dataclass(frozen=True, kw_only=True)
class AmtronSensorEntityDescription(SensorEntityDescription):
    """Describe one sensor backed by a register."""

    register_key: str
    enum_class: type[IntEnum] | None = None
    value_fn: Callable[[RegisterValue], str | float | None] | None = None


def _enum(
    key: str,
    enum_class: type[IntEnum],
    *,
    register_key: str | None = None,
    entity_category: EntityCategory | None = None,
    value_fn: Callable[[RegisterValue], str | float | None] | None = None,
) -> AmtronSensorEntityDescription:
    return AmtronSensorEntityDescription(
        key=key,
        translation_key=key,
        register_key=register_key or key,
        device_class=SensorDeviceClass.ENUM,
        options=list(enum_options(enum_class)),
        enum_class=enum_class,
        entity_category=entity_category,
        value_fn=value_fn,
    )


def _fallback_behaviour_option(value: RegisterValue) -> str | None:
    """Map 0x030E onto what it asks the wallbox to do."""

    if not isinstance(value, int):
        return None
    behaviour = ems_fallback_behaviour(value)
    return enum_option(behaviour) if behaviour is not None else None


def _fallback_current(value: RegisterValue) -> float | None:
    """Return 0x030E only when it really holds a charging current."""

    return ems_fallback_current(value) if isinstance(value, int) else None


def _error_category_option(value: RegisterValue) -> str | None:
    """Map a raw error code onto its documented category option."""

    if not isinstance(value, int):
        return None
    return enum_option(error_category(value))


def _current(
    key: str,
    *,
    entity_category: EntityCategory | None = None,
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT,
) -> AmtronSensorEntityDescription:
    return AmtronSensorEntityDescription(
        key=key,
        translation_key=key,
        register_key=key,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=state_class,
        suggested_display_precision=2,
        entity_category=entity_category,
    )


SENSOR_DESCRIPTIONS: tuple[AmtronSensorEntityDescription, ...] = (
    _enum("evse_state", EvseState),
    _enum("cp_state", CpState, entity_category=EntityCategory.DIAGNOSTIC),
    _enum("authorization_status", AuthorizationStatus),
    _enum(
        "error_code",
        ErrorCategory,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error_category_option,
    ),
    _enum("detected_ev_phases", DetectedEvPhases),
    _enum("switched_phases", SwitchedPhases),
    _current("signaled_current"),
    _current("current_l1"),
    _current("current_l2"),
    _current("current_l3"),
    AmtronSensorEntityDescription(
        key="voltage_l1",
        entity_registry_enabled_default=False,
        translation_key="voltage_l1",
        register_key="voltage_l1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AmtronSensorEntityDescription(
        key="voltage_l2",
        entity_registry_enabled_default=False,
        translation_key="voltage_l2",
        register_key="voltage_l2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AmtronSensorEntityDescription(
        key="voltage_l3",
        entity_registry_enabled_default=False,
        translation_key="voltage_l3",
        register_key="voltage_l3",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AmtronSensorEntityDescription(
        key="power_l1",
        entity_registry_enabled_default=False,
        translation_key="power_l1",
        register_key="power_l1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AmtronSensorEntityDescription(
        key="power_l2",
        entity_registry_enabled_default=False,
        translation_key="power_l2",
        register_key="power_l2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AmtronSensorEntityDescription(
        key="power_l3",
        entity_registry_enabled_default=False,
        translation_key="power_l3",
        register_key="power_l3",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AmtronSensorEntityDescription(
        key="power_total",
        translation_key="power_total",
        register_key="power_total",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    _current("session_max_current", state_class=None),
    AmtronSensorEntityDescription(
        key="session_energy",
        translation_key="session_energy",
        register_key="session_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
    ),
    AmtronSensorEntityDescription(
        key="session_duration",
        translation_key="session_duration",
        register_key="session_duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        # Without a display precision the frontend shows hours:minutes:seconds.
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
    AmtronSensorEntityDescription(
        key="sessions_total",
        translation_key="sessions_total",
        register_key="sessions_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
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
    AmtronSensorEntityDescription(
        key="modbus_layout_version",
        translation_key="modbus_layout_version",
        register_key="modbus_layout_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda value: layout_label(value) if isinstance(value, int) else None,
    ),
    _current("downgrade_current", entity_category=EntityCategory.DIAGNOSTIC),
    _current(
        "max_current_house",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
    ),
    _current(
        "max_evse_current",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
    ),
    _enum(
        "ems_fallback_behaviour",
        EmsFallbackBehaviour,
        register_key="ems_fallback_current",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_fallback_behaviour_option,
    ),
    AmtronSensorEntityDescription(
        key="ems_fallback_current",
        translation_key="ems_fallback_current",
        register_key="ems_fallback_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_fallback_current,
    ),
    _current(
        "grid_imbalance_threshold",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
    ),
    _current(
        "solar_min_current",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=None,
    ),
    AmtronSensorEntityDescription(
        key="phase_switching_pause",
        translation_key="phase_switching_pause",
        register_key="phase_switching_pause",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _enum("phase_rotation", PhaseRotation, entity_category=EntityCategory.DIAGNOSTIC),
    _enum(
        "cable_lock_status",
        CableLockStatus,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _enum(
        "phase_switching_mode",
        PhaseSwitchingMode,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _enum(
        "grid_phases_connected",
        GridPhasesConnected,
        entity_category=EntityCategory.DIAGNOSTIC,
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
