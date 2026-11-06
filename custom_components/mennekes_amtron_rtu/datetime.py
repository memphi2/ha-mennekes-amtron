"""One-off departure time control for a vehicle charge target."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.runtime_data.timed_goal is not None:
        async_add_entities([AmtronDepartureTime(entry)])


class AmtronDepartureTime(MennekesAmtronEntity, DateTimeEntity):
    """The next trip only; selecting a calendar event sets this same value."""

    _attr_translation_key = "timed_departure"

    def __init__(self, entry: MennekesAmtronConfigEntry) -> None:
        super().__init__(entry, entry.runtime_data.coordinator, "timed_departure")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and not self.runtime_data.control.read_only

    @property
    def native_value(self) -> datetime | None:
        observer = self.runtime_data.timed_goal
        return observer.goal.deadline if observer is not None else None

    async def async_set_value(self, value: datetime) -> None:
        observer = self.runtime_data.timed_goal
        if observer is not None:
            await observer.async_set_deadline(value)
