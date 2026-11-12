"""Static binary-sensor descriptions, derived from the register map."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntityDescription

from .data import WallboxData
from .device.decode import RegisterValue
from .device.enums import (
    DowngradeStatus,
    EvseState,
    ems_fallback_behaviour,
    ems_fallback_current,
    enum_option,
    error_category,
)
from .device.vehicle import vehicle_connected
from .entity_attributes import AttributeReaders, integer_attribute, number_attribute

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
else:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass

@dataclass(frozen=True, kw_only=True)
class AmtronBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe one binary sensor derived from a register, with its details."""

    register_key: str
    is_on_fn: Callable[[RegisterValue], bool | None]
    attributes: AttributeReaders = field(default_factory=dict)


def _is_flag(value: RegisterValue) -> bool | None:
    if not isinstance(value, int):
        return None
    return value == 1


def _error_category(data: WallboxData) -> str | None:
    value = data.get("error_code")
    return enum_option(error_category(value)) if isinstance(value, int) else None


def _fallback_behaviour(data: WallboxData) -> str | None:
    """What 0x030E asks the wallbox to do without a heartbeat."""
    value = data.get("ems_fallback_current")
    behaviour = ems_fallback_behaviour(value) if isinstance(value, int) else None
    return enum_option(behaviour) if behaviour is not None else None


def _fallback_current(data: WallboxData) -> float | None:
    """The fallback current, only when 0x030E really holds one."""
    value = data.get("ems_fallback_current")
    return ems_fallback_current(value) if isinstance(value, int) else None


BINARY_SENSOR_DESCRIPTIONS: tuple[AmtronBinarySensorEntityDescription, ...] = (
    AmtronBinarySensorEntityDescription(
        key="vehicle_connected",
        translation_key="vehicle_connected",
        register_key="evse_state",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=lambda value: (
            vehicle_connected(value, None, cp_supported=False)
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
        attributes={"current": number_attribute("downgrade_current", 2)},
    ),
    AmtronBinarySensorEntityDescription(
        key="error",
        translation_key="error",
        register_key="error_code",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda value: (value != 0 if isinstance(value, int) else None),
        # The register is a plain number and the specification documents only
        # a few of them: the raw code stays visible beside its category.
        attributes={"code": integer_attribute("error_code"), "category": _error_category},
    ),
    AmtronBinarySensorEntityDescription(
        key="master_lost_fallback",
        translation_key="master_lost_fallback",
        register_key="master_lost_fallback",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_is_flag,
        attributes={"behaviour": _fallback_behaviour, "fallback_current": _fallback_current},
    ),
)
