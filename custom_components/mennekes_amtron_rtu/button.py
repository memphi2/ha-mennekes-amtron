"""Buttons: the few actions that have no state of their own."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .device.registers import ERROR_CODE, SYSTEM_RESTART
from .entity import AmtronEntity, Projection, Runtime, wanted
from .entry_types import MennekesAmtronConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.button.const import ButtonDeviceClass
else:
    from homeassistant.components.button import ButtonDeviceClass

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AmtronButtonDescription(Projection, ButtonEntityDescription):
    press: Callable[[Runtime], Awaitable[object]]


BUTTONS: tuple[AmtronButtonDescription, ...] = (
    # The documented sequence after a heartbeat loss.
    AmtronButtonDescription(
        key="recover_from_error", register=ERROR_CODE.key, writes=True,
        entity_category=EntityCategory.CONFIG,
        press=lambda runtime: runtime.control.async_recover_from_error(),
    ),
    # Allowed only in the idle state; the service refuses it with a car connected.
    AmtronButtonDescription(
        key="restart", capability=SYSTEM_RESTART.key, register="evse_state", writes=True,
        device_class=ButtonDeviceClass.RESTART, entity_category=EntityCategory.CONFIG,
        press=lambda runtime: runtime.control.async_restart(),
    ),
    # Removes a queued change without changing the current charging state.
    AmtronButtonDescription(
        key="cancel_pending_change", offline=True,
        ready=lambda runtime: runtime.control.can_cancel_pending,
        press=lambda runtime: runtime.control.async_cancel_pending_change(),
    ),
    AmtronButtonDescription(
        key="guest_start", writes=True,
        ready=lambda runtime: runtime.guest.active is not None,
        press=lambda runtime: runtime.guest.async_start_guest(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(AmtronButton(entry, description) for description in wanted(entry, BUTTONS))


class AmtronButton(AmtronEntity, ButtonEntity):
    entity_description: AmtronButtonDescription

    async def async_press(self) -> None:
        await self.entity_description.press(self.runtime)
