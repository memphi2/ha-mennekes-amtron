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

from .decode import RegisterValue
from .enums import (
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
from .registers import layout_label


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
    _enum("cp_state", CpState),
    _enum("authorization_status", AuthorizationStatus),
    _enum("error_code", ErrorCategory, value_fn=_error_category_option),
    _enum("detected_ev_phases", DetectedEvPhases),
    _current("signaled_current"),
    _current("current_l1"),
    _current("current_l2"),
    _current("current_l3"),
    AmtronSensorEntityDescription(
        key="voltage_l1",
        translation_key="voltage_l1",
        register_key="voltage_l1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AmtronSensorEntityDescription(
        key="voltage_l2",
        translation_key="voltage_l2",
        register_key="voltage_l2",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AmtronSensorEntityDescription(
        key="voltage_l3",
        translation_key="voltage_l3",
        register_key="voltage_l3",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AmtronSensorEntityDescription(
        key="power_l1",
        translation_key="power_l1",
        register_key="power_l1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AmtronSensorEntityDescription(
        key="power_l2",
        translation_key="power_l2",
        register_key="power_l2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AmtronSensorEntityDescription(
        key="power_l3",
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
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
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
        value_fn=lambda value: (
            layout_label(value) if isinstance(value, int) else None
        ),
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
        "switched_phases",
        SwitchedPhases,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
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
