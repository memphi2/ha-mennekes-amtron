"""Optional departure entities follow connectivity and service write access."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu import vehicle_features as features
from custom_components.mennekes_amtron_rtu.button import (
    async_setup_entry as setup_buttons,
)
from custom_components.mennekes_amtron_rtu.datetime import (
    async_setup_entry as setup_datetime,
)
from custom_components.mennekes_amtron_rtu.device.departure_plan import (
    DepartureForecast,
)
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from tests.ha_fakes import build_runtime, collect


async def _entities(hass, entry):
    datetimes, buttons, sensors = {}, {}, {}
    await setup_datetime(hass, entry, lambda items: datetimes.update(collect(items)))
    await setup_buttons(hass, entry, lambda items: buttons.update(collect(items)))
    await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
    return datetimes, buttons, sensors


def _timed_observer():
    return SimpleNamespace(
        goal=SimpleNamespace(deadline=datetime(2026, 11, 10, 7, tzinfo=UTC)),
        status="paced", planned_start=None, forecast=None,
        async_set_deadline=AsyncMock(),
        async_use_next_calendar_event=AsyncMock(),
        async_use_vehicle_departure=AsyncMock(),
        vehicle_departure_available=False,
    )


def _plan_departures(entry):
    # Entities exist once a vehicle uses departure planning.
    entry.options.update(vehicle_soc_entity="sensor.car_soc", timed_goal_enabled=True)


def test_departure_entities_are_absent_when_timed_goals_are_disabled():
    async def run():
        hass, entry, transport = await build_runtime()
        datetimes, buttons, sensors = await _entities(hass, entry)
        assert not datetimes
        assert "timed_clear_departure" not in buttons
        assert "timed_next_calendar_event" not in buttons
        assert "timed_goal_status" not in sensors
        assert not transport.writes
        await entry.runtime_data.control.async_shutdown()

    asyncio.run(run())


def test_departure_status_remains_visible_without_calendar_configuration():
    async def run():
        hass, entry, transport = await build_runtime()
        _plan_departures(entry)
        entry.runtime_data.timed_goal = _timed_observer()
        datetimes, buttons, sensors = await _entities(hass, entry)
        assert "timed_next_calendar_event" not in buttons
        assert buttons["timed_clear_departure"].available
        assert datetimes["timed_departure"].available
        status = sensors["timed_goal_status"]
        assert status.available
        assert status.native_value == "paced"
        entry.runtime_data.timed_goal.status = "waiting_for_soc"
        assert status.native_value == "waiting_for_soc"
        # The service's forecast is shown beside the planned start.
        assert status.extra_state_attributes is None
        finish = datetime(2026, 11, 10, 7, 30, tzinfo=UTC)
        entry.runtime_data.timed_goal.planned_start = datetime(2026, 11, 10, 3, tzinfo=UTC)
        entry.runtime_data.timed_goal.forecast = DepartureForecast(finish, 74.0, 0.88, None)
        assert status.extra_state_attributes == {
            "planned_start": datetime(2026, 11, 10, 3, tzinfo=UTC), "estimated_finish": finish,
            "estimated_level_at_departure": 74.0, "learned_efficiency": 0.88,
        }
        entry.runtime_data.coordinator.last_update_success = False
        assert not status.available
        assert not datetimes["timed_departure"].available
        assert not buttons["timed_clear_departure"].available
        entry.runtime_data.coordinator.last_update_success = True
        entry.runtime_data.timed_goal = None
        # A car without departure planning leaves the entities unavailable.
        assert status.native_value is None
        assert not status.available
        assert not datetimes["timed_departure"].available
        assert not buttons["timed_clear_departure"].available
        assert not transport.writes
        await entry.runtime_data.control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("remote", [False, True])
def test_departure_controls_respect_read_only_but_status_is_observable(remote, monkeypatch):
    async def run():
        hass, entry, transport = await build_runtime(read_only=True)
        if remote:
            entry.data["server_url"] = "http://service:8765"
        entry.options["timed_calendar_entity"] = "calendar.trips"
        _plan_departures(entry)
        monkeypatch.setattr(features, "resolve_departure_source", lambda *_: object())
        entry.runtime_data.timed_goal = _timed_observer()
        entry.runtime_data.timed_goal.vehicle_departure_available = True
        datetimes, buttons, sensors = await _entities(hass, entry)
        assert not datetimes["timed_departure"].available
        assert sensors["timed_goal_status"].available
        if remote:
            assert not buttons["timed_next_calendar_event"].available
            assert not buttons["timed_clear_departure"].available
            assert not buttons["timed_vehicle_departure"].available
        else:
            assert "timed_next_calendar_event" not in buttons
            assert "timed_clear_departure" not in buttons
            assert "timed_vehicle_departure" not in buttons
        assert not transport.writes
        await entry.runtime_data.control.async_shutdown()

    asyncio.run(run())


def test_vehicle_import_button_keeps_its_identity_and_tracks_provider_availability(monkeypatch):
    async def run():
        hass, entry, transport = await build_runtime()
        _plan_departures(entry)
        entry.runtime_data.timed_goal = observer = _timed_observer()
        _, buttons, _ = await _entities(hass, entry)
        assert "timed_vehicle_departure" not in buttons
        monkeypatch.setattr(features, "resolve_departure_source", lambda *_: object())
        _, buttons, _ = await _entities(hass, entry)
        button = buttons["timed_vehicle_departure"]
        assert button.unique_id.endswith("_timed_bmw_departure")
        assert button.translation_key == "timed_vehicle_departure"
        assert not button.available
        observer.vehicle_departure_available = True
        assert button.available
        await button.async_press()
        observer.async_use_vehicle_departure.assert_awaited_once()
        entry.runtime_data.timed_goal = None
        assert not button.available
        await button.async_press()
        observer.async_use_vehicle_departure.assert_awaited_once()
        assert not transport.writes
        await entry.runtime_data.control.async_shutdown()
    asyncio.run(run())
