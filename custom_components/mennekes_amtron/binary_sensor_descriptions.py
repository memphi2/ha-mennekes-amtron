"""Static binary-sensor descriptions, derived from the register map."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .decode import RegisterValue
from .enums import (
    DowngradeStatus,
    EvseState,
    PhaseOptionsHardware,
)

VEHICLE_CONNECTED_STATES = frozenset(
    {
        EvseState.EV_CONNECTED,
        EvseState.PRECONDITIONS_VALID,
        EvseState.READY_TO_CHARGE,
        EvseState.CHARGING,
    }
)


@dataclass(frozen=True, kw_only=True)
class AmtronBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe one binary sensor derived from a register."""

    register_key: str
    is_on_fn: Callable[[RegisterValue], bool | None]


def _is_flag(value: RegisterValue) -> bool | None:
    if not isinstance(value, int):
        return None
    return value == 1


BINARY_SENSOR_DESCRIPTIONS: tuple[AmtronBinarySensorEntityDescription, ...] = (
    AmtronBinarySensorEntityDescription(
        key="vehicle_connected",
        translation_key="vehicle_connected",
        register_key="evse_state",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=lambda value: (
            value in VEHICLE_CONNECTED_STATES if isinstance(value, int) else None
        ),
    ),
    AmtronBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        register_key="evse_state",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        is_on_fn=lambda value: (
            value == EvseState.CHARGING if isinstance(value, int) else None
        ),
    ),
    AmtronBinarySensorEntityDescription(
        key="downgrade_active",
        translation_key="downgrade_active",
        register_key="downgrade_status",
        is_on_fn=lambda value: (
            value == DowngradeStatus.DOWNGRADED if isinstance(value, int) else None
        ),
    ),
    AmtronBinarySensorEntityDescription(
        key="error",
        translation_key="error",
        register_key="error_code",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda value: (value != 0 if isinstance(value, int) else None),
    ),
    AmtronBinarySensorEntityDescription(
        key="master_lost_fallback",
        translation_key="master_lost_fallback",
        register_key="master_lost_fallback",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_is_flag,
    ),
    AmtronBinarySensorEntityDescription(
        key="authorization_enabled",
        translation_key="authorization_enabled",
        register_key="authorization_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_is_flag,
    ),
    AmtronBinarySensorEntityDescription(
        key="grid_imbalance_enabled",
        translation_key="grid_imbalance_enabled",
        register_key="grid_imbalance",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_is_flag,
    ),
    AmtronBinarySensorEntityDescription(
        key="cable_lock_enabled",
        translation_key="cable_lock_enabled",
        register_key="cable_lock_setting",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_is_flag,
    ),
    AmtronBinarySensorEntityDescription(
        key="phase_switch_capable",
        translation_key="phase_switch_capable",
        register_key="phase_options_hw",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda value: (
            value == PhaseOptionsHardware.ONE_OR_THREE_PHASES
            if isinstance(value, int)
            else None
        ),
    ),
)
