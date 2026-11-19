"""The time the car should be ready, or a guest charge should end."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity, DateTimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.persistence import StorageCommitUncertainError, StorageStateUnknownError
from .entity import AmtronEntity, Projection
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import home_assistant_error

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class _Description(Projection, DateTimeEntityDescription):
    pass


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AmtronTargetTime(entry, _Description(key="timed_departure", writes=True))])


class AmtronTargetTime(AmtronEntity, DateTimeEntity):
    """One time for whoever charges: the selected car's trip, or the guest.

    The selected car's trip is kept by its observer, which also takes it from
    a calendar or the car; without one, the service keeps the time itself.
    """

    @property
    def _owns_trip(self) -> bool:
        return self.runtime.timed_goal is not None and not self.runtime.control.guest_active()

    @property
    def available(self) -> bool:
        return super().available and (
            self.runtime.timed_goal is not None or self.runtime.control.departure is not None
        )

    @property
    def native_value(self) -> datetime | None:
        if self._owns_trip and (observer := self.runtime.timed_goal) is not None:
            return observer.goal.deadline
        plan = self.runtime.control.departure
        return plan.trip.deadline if plan is not None else None

    async def _async_set(self, value: datetime | None) -> None:
        if self._owns_trip and (observer := self.runtime.timed_goal) is not None:
            try:
                await observer.async_set_deadline(value)
            except (StorageCommitUncertainError, StorageStateUnknownError, OSError) as err:
                # Home Assistant's own storage refused, not the wallbox.
                raise home_assistant_error("departure_save_failed") from err
        else:
            await self.runtime.control.async_set_target_time(value)

    async def async_set_value(self, value: datetime) -> None:
        await self._async_set(value)

    async def async_clear_value(self) -> None:
        await self._async_set(None)
