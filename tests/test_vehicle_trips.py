"""Each car's open departure is parked while another car is selected."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import vehicle_trips as m
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.timed_goal import TimedGoal
from tests.ha_fakes import FakeConfigEntry, FakeHass


def trip(hours: float = 8, *, finished: bool = False) -> TimedGoal:
    goal = TimedGoal()
    goal.set(dt_util.utcnow().replace(microsecond=0) + timedelta(hours=hours), "manual")
    if finished:
        goal.finish("reached")
    return goal


def test_open_trips_are_parked_and_resumed_once_per_car(tmp_path):
    async def run():
        memory = m.VehicleTripMemory(FakeHass(str(tmp_path)), "entry", "serial")
        first, second = trip(), trip(10)
        assert await memory.async_swap("a", first, "b") is None
        assert (await memory.async_swap("b", second, "a")).snapshot() == first.snapshot()
        resumed = await m.VehicleTripMemory(FakeHass(str(tmp_path)), "entry", "serial").async_swap(
            "a", None, "b",
        )
        assert resumed.snapshot() == second.snapshot()
        # Resuming consumes the parked trip; a cleared or finished trip is not kept.
        assert await memory.async_swap(None, None, "b") is None
        assert await memory.async_swap("a", trip(finished=True), "a") is None
        assert await memory.async_swap("a", trip(-1), "a") is None
        await memory.async_swap("a", trip(), None)
        await memory.async_forget("a")
        assert await memory.async_swap(None, None, "a") is None

    asyncio.run(run())


def test_parking_is_bounded_and_drops_the_oldest_car(tmp_path):
    async def run():
        memory = m.VehicleTripMemory(FakeHass(str(tmp_path)), "entry", "serial")
        for index in range(m.MAX_TRIPS + 1):
            await memory.async_swap(str(index), trip(), None)
        assert await memory.async_swap(None, None, "0") is None
        assert await memory.async_swap(None, None, str(m.MAX_TRIPS)) is not None

    asyncio.run(run())


@pytest.mark.parametrize("raw", [
    None, {"trips": []}, {"trips": {}, "extra": 1}, {"trips": {"": trip().snapshot()}},
    {"trips": {str(index): trip().snapshot() for index in range(m.MAX_TRIPS + 1)}},
    {"trips": {"a": {"deadline": 1}}},
])
def test_invalid_saved_trips_are_rejected(raw):
    with pytest.raises(ValueError, match="invalid"):
        m.VehicleTrips.restore(raw)


def test_storage_problems_only_forget_parked_trips(tmp_path, caplog):
    async def run():
        hass = FakeHass(str(tmp_path))
        memory = m.VehicleTripMemory(hass, "entry", "serial")
        with patch.object(m.VehicleTripStore, "async_save", AsyncMock(side_effect=OSError("disk"))):
            await memory.async_swap("a", trip(), None)
        assert "not remembered" in caplog.text
        await memory.async_swap("a", trip(), None)
        # Another wallbox's file is ignored instead of resuming its trip.
        other = m.VehicleTripMemory(hass, "entry", "other")
        assert await other.async_swap(None, None, "a") is None
        assert "Ignoring saved vehicle departures" in caplog.text

    asyncio.run(run())


def test_a_resumed_trip_that_cannot_be_saved_leaves_the_car_without_one(tmp_path, caplog):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry(options={
            "timed_goal_reset_pending": True, "vehicle_soc_entity": "sensor.soc",
            "timed_goal_enabled": True,
        })
        entry.runtime_data = SimpleNamespace(identity=SimpleNamespace(serial_number="serial"))
        with patch.object(integration, "vehicle_options", return_value={
            **entry.options, "vehicle_profile_id": "a",
        }):
            await m.VehicleTripMemory(hass, entry.entry_id, "serial").async_swap("a", trip(), None)
            with patch.object(TimedGoalStore, "async_save", AsyncMock(side_effect=OSError("disk"))):
                await integration._async_resume_trip(hass, entry, True)
        assert "not resumed" in caplog.text
        assert (await TimedGoalStore(hass, entry.entry_id, "serial").async_load()).deadline is None

    asyncio.run(run())


def test_a_deleted_cars_trip_is_dropped_instead_of_parked(tmp_path):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry(options={"timed_goal_reset_pending": True})
        entry.runtime_data = SimpleNamespace(
            identity=SimpleNamespace(serial_number="serial"), vehicle_profile_id="a" * 32,
            timed_goal=SimpleNamespace(goal=trip()),
        )
        memory = m.VehicleTripMemory(hass, entry.entry_id, "serial")
        await memory.async_swap("a" * 32, trip(), None)
        await integration._async_park_trip(hass, entry)
        assert await memory.async_swap(None, None, "a" * 32) is None

    asyncio.run(run())
