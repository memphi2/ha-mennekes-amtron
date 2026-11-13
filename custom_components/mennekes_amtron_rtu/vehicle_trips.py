"""Remember each vehicle's planned departure across vehicle changes."""

from __future__ import annotations

import logging
from typing import Any, Self

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .device.timed_goal import TimedGoal
from .goal_store import ModelStore

_LOGGER = logging.getLogger(__name__)
# More than the number of profiles a household configures; oldest drop first.
MAX_TRIPS = 16


class VehicleTrips:
    """Open departures of the vehicles that are not selected, by profile."""

    def __init__(self, trips: dict[str, TimedGoal] | None = None) -> None:
        self.trips = dict(trips or {})

    def snapshot(self) -> dict[str, Any]:
        return {"trips": {key: trip.snapshot() for key, trip in self.trips.items()}}

    @classmethod
    def restore(cls, raw: object) -> Self:
        if not isinstance(raw, dict) or raw.keys() != {"trips"} or not isinstance(raw["trips"], dict):
            raise ValueError("invalid saved vehicle trips")
        trips = raw["trips"]
        if len(trips) > MAX_TRIPS or not all(
            isinstance(key, str) and 0 < len(key) <= 64 for key in trips
        ):
            raise ValueError("invalid saved vehicle trips")
        return cls({key: TimedGoal.restore(trip) for key, trip in trips.items()})


class VehicleTripStore(ModelStore[VehicleTrips]):
    """Keep parked trips in their own file, outside the selected car's trip."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="vehicle_trips", model=VehicleTrips)


def _open(trip: TimedGoal | None) -> bool:
    return (
        trip is not None and trip.finished is None
        and trip.deadline is not None and trip.deadline > dt_util.utcnow()
    )


class VehicleTripMemory:
    """Best-effort memory; a storage problem only forgets a parked trip."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        self._store = VehicleTripStore(hass, entry_id, identity)

    async def _async_load(self) -> VehicleTrips:
        try:
            return await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Ignoring saved vehicle departures: %s", err)
            return VehicleTrips()

    async def _async_save(self, trips: VehicleTrips) -> None:
        try:
            await self._store.async_save(trips)
        except OSError as err:
            _LOGGER.warning("Vehicle departure not remembered: %s", err)

    async def async_swap(
        self, previous: str | None, trip: TimedGoal | None, selected: str | None,
    ) -> TimedGoal | None:
        """Park the previous car's open trip and return the selected car's."""
        trips = await self._async_load()
        before = trips.snapshot()
        if previous is not None:
            trips.trips.pop(previous, None)
            if _open(trip):
                trips.trips[previous] = TimedGoal.restore(trip.snapshot())  # type: ignore[union-attr]
        resumed = trips.trips.pop(selected, None) if selected is not None else None
        while len(trips.trips) > MAX_TRIPS:
            del trips.trips[next(iter(trips.trips))]
        if trips.snapshot() != before:
            await self._async_save(trips)
        return resumed if _open(resumed) else None

    async def async_retain(self, planning: set[str]) -> None:
        """Drop the parked trips of cars that no longer plan departures."""
        trips = await self._async_load()
        if trips.trips.keys() <= planning:
            return
        trips.trips = {key: trip for key, trip in trips.trips.items() if key in planning}
        await self._async_save(trips)
