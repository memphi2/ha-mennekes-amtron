"""Switches: pause, charging release, lock and Plug & Charge."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.registers import CHARGING_CURRENT_EMS, CHARGING_RELEASE, LOCK_EVSE
from .entity import AmtronEntity, Projection, Runtime, wanted
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error

if TYPE_CHECKING:
    from homeassistant.components.switch.const import SwitchDeviceClass
else:
    from homeassistant.components.switch import SwitchDeviceClass

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AmtronSwitchDescription(Projection, SwitchEntityDescription):
    is_on: Callable[[Runtime], bool | None]
    turn: Callable[[Runtime, bool], Awaitable[None]]


def _flag(key: str) -> Callable[[Runtime], bool | None]:
    def read(runtime: Runtime) -> bool | None:
        value = runtime.coordinator.current_data().get(key)
        return value == 1 if isinstance(value, int) else None

    return read


async def _pause(runtime: Runtime, on: bool) -> None:
    if on:
        await runtime.control.async_pause_charging()
    else:
        await runtime.control.async_resume_charging()


async def _plug_charge(runtime: Runtime, on: bool) -> None:
    if runtime.plug_charge is None:
        raise service_validation_error("plug_charge_unavailable")
    await runtime.plug_charge.async_set_enabled(on)


def _plug_charge_enabled(runtime: Runtime) -> bool:
    return runtime.control.plug_charge.get("enabled") is True


SWITCHES: tuple[AmtronSwitchDescription, ...] = (
    # Signals 0 A to the car and leaves the relay closed: the way to pause.
    AmtronSwitchDescription(
        key="charging_paused", register=CHARGING_CURRENT_EMS.key, writes=True,
        is_on=lambda runtime: runtime.control.is_paused, turn=_pause,
    ),
    # Opens and closes the relay, which wears it out: not for everyday pausing.
    AmtronSwitchDescription(
        key="charging_release", register=CHARGING_RELEASE.key, writes=True,
        device_class=SwitchDeviceClass.SWITCH, is_on=_flag(CHARGING_RELEASE.key),
        turn=lambda runtime, on: runtime.control.async_set_charging_release(on),
    ),
    AmtronSwitchDescription(
        key="lock_evse", register=LOCK_EVSE.key, writes=True, is_on=_flag(LOCK_EVSE.key),
        turn=lambda runtime, on: runtime.control.async_set_lock_evse(on),
    ),
    AmtronSwitchDescription(
        key="virtual_plug_charge", entity_category=EntityCategory.CONFIG,
        exists=lambda runtime: runtime.plug_charge is not None,
        # Switching it off stays possible while the service allows no writes.
        ready=lambda runtime: not runtime.control.read_only or _plug_charge_enabled(runtime),
        is_on=_plug_charge_enabled, turn=_plug_charge,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(AmtronSwitch(entry, description) for description in wanted(hass, entry, SWITCHES))


class AmtronSwitch(AmtronEntity, SwitchEntity):
    entity_description: AmtronSwitchDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on(self.runtime)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.turn(self.runtime, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.turn(self.runtime, False)
