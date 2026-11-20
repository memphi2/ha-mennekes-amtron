"""The time the car should be ready, or a guest charge should end."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity, DateTimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import AmtronEntity, Projection
from .entry_types import MennekesAmtronConfigEntry

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class _Description(Projection, DateTimeEntityDescription):
    pass


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AmtronTargetTime(entry, _Description(key="departure_time", writes=True))])


class AmtronTargetTime(AmtronEntity, DateTimeEntity):
    """One time for whoever charges: the selected car's departure, or a guest's end.

    The service keeps it. The selected car's observer adds what only Home
    Assistant knows about a time set here: which calendar or car time it replaces.
    """

    @property
    def available(self) -> bool:
        return super().available and self.runtime.control.departure is not None

    @property
    def native_value(self) -> datetime | None:
        plan = self.runtime.control.departure
        return plan.trip.deadline if plan is not None else None

    async def _async_set(self, value: datetime | None) -> None:
        if (observer := self.runtime.departure_observer) is not None:
            await observer.async_set_deadline(value)
        else:
            await self.runtime.control.async_set_target_time(value)

    async def async_set_value(self, value: datetime) -> None:
        await self._async_set(value)

    async def async_clear_value(self) -> None:
        await self._async_set(None)
