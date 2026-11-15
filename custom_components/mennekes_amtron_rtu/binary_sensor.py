"""Binary sensors: a car is plugged in, it charges, something is wrong."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.enums import (
    EvseState,
    ems_fallback_behaviour,
    ems_fallback_current,
    enum_option,
    error_category,
)
from .device.registers import CP_STATE
from .device.vehicle import vehicle_connected
from .entity import AmtronEntity, Projection, Runtime, wanted
from .entry_types import MennekesAmtronConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
else:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AmtronBinarySensorDescription(Projection, BinarySensorEntityDescription):
    is_on: Callable[[Runtime], bool | None]


def _connected(runtime: Runtime) -> bool | None:
    # The pilot state keeps the car present during a wallbox fault.
    data = runtime.coordinator.current_data()
    return vehicle_connected(
        data.get("evse_state"), data.get(CP_STATE.key),
        cp_supported=runtime.identity.layout_version >= CP_STATE.min_layout,
    )


def _charging(runtime: Runtime) -> bool | None:
    value = runtime.coordinator.current_data().get("evse_state")
    return value == EvseState.CHARGING if isinstance(value, int) else None


def _error(runtime: Runtime) -> bool | None:
    value = runtime.coordinator.current_data().get("error_code")
    return value != 0 if isinstance(value, int) else None


def _error_details(runtime: Runtime) -> dict[str, Any]:
    """The raw code beside its category: the specification names only a few.

    A lost heartbeat shows here what the wallbox does about it.
    """
    data = runtime.coordinator.current_data()
    code = data.get("error_code")
    fallback = data.get("ems_fallback_current")
    lost = data.get("master_lost_fallback") == 1
    behaviour = ems_fallback_behaviour(fallback) if lost and isinstance(fallback, int) else None
    return {
        "code": code if isinstance(code, int) else None,
        "category": enum_option(error_category(code)) if isinstance(code, int) else None,
        "heartbeat_lost": True if lost else None,
        "fallback": enum_option(behaviour) if behaviour is not None else None,
        "fallback_current": ems_fallback_current(fallback) if lost and isinstance(fallback, int) else None,
    }


BINARY_SENSORS: tuple[AmtronBinarySensorDescription, ...] = (
    AmtronBinarySensorDescription(
        key="vehicle_connected", register="evse_state", is_on=_connected,
        device_class=BinarySensorDeviceClass.PLUG,
    ),
    AmtronBinarySensorDescription(
        key="charging", register="evse_state", is_on=_charging,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    ),
    AmtronBinarySensorDescription(
        key="error", register="error_code", is_on=_error,
        device_class=BinarySensorDeviceClass.PROBLEM, attributes=_error_details,
    ),
    AmtronBinarySensorDescription(
        key="guest_charging", is_on=lambda runtime: runtime.guest.active,
        ready=lambda runtime: runtime.guest.active is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        AmtronBinarySensor(entry, description) for description in wanted(hass, entry, BINARY_SENSORS)
    )


class AmtronBinarySensor(AmtronEntity, BinarySensorEntity):
    entity_description: AmtronBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on(self.runtime)
