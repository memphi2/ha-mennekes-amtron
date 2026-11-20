"""Departure entities follow connectivity, write access and the selected car."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.datetime import (
    async_setup_entry as setup_datetime,
)
from custom_components.mennekes_amtron_rtu.device.departure_plan import (
    DepartureForecast,
)
from custom_components.mennekes_amtron_rtu.device.operation import Operation
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.services import DEVICE_ACTIONS
from tests.ha_fakes import build_runtime, collect
from tests.test_vehicle_profile_runtime import A, catalog

PLAN = ("departure_plan", "planned_start", "estimated_finish", "estimated_level_at_departure")


async def _entities(hass, entry):
    datetimes, sensors = {}, {}
    await setup_datetime(hass, entry, lambda items: datetimes.update(collect(items)))
    await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
    return datetimes, sensors


def _departure_observer():
    return SimpleNamespace(
        goal=SimpleNamespace(deadline=datetime(2026, 11, 10, 7, tzinfo=UTC)),
        status="charging", planned_start=None, forecast=None,
        async_set_deadline=AsyncMock(),
        async_use_next_calendar_event=AsyncMock(),
        async_use_vehicle_departure=AsyncMock(),
    )


def _plan_departures(entry):
    entry.options.update(vehicle_soc_entity="sensor.car_soc", departure_enabled=True)


def test_without_departure_planning_the_service_keeps_the_time_and_no_plan_is_shown():
    async def run():
        hass, entry, transport = await build_runtime()
        datetimes, sensors = await _entities(hass, entry)
        departure = datetimes["departure_time"]
        assert departure.available
        assert departure.native_value is None
        waiting = sensors["operation_waiting"]
        assert waiting.available
        assert not set(PLAN) & set(waiting.extra_state_attributes or {})
        assert not transport.writes
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_departure_status_and_forecast_are_shown_with_the_services_summary():
    async def run():
        hass, entry, transport = await build_runtime()
        _plan_departures(entry)
        runtime = entry.runtime_data
        runtime.departure_observer = observer = _departure_observer()
        datetimes, sensors = await _entities(hass, entry)
        departure, waiting = datetimes["departure_time"], sensors["operation_waiting"]
        assert departure.available
        # The time shown is the service's, not a copy the observer keeps.
        assert departure.native_value is None
        assert waiting.available
        details = waiting.extra_state_attributes
        assert details["departure_plan"] == "charging"
        assert not {"planned_start", "estimated_finish", "estimated_level_at_departure"} & details.keys()
        observer.status = "waiting_for_soc"
        assert waiting.extra_state_attributes["departure_plan"] == "waiting_for_soc"
        # The service's forecast is shown beside the planned start.
        start, finish = datetime(2026, 11, 10, 3, tzinfo=UTC), datetime(2026, 11, 10, 7, 30, tzinfo=UTC)
        observer.planned_start = start
        observer.forecast = DepartureForecast(finish, 74.0, 0.88, None)
        details = waiting.extra_state_attributes
        assert {key: details[key] for key in PLAN} == {
            "departure_plan": "waiting_for_soc", "planned_start": start,
            "estimated_finish": finish, "estimated_level_at_departure": 74.0,
        }
        runtime.coordinator.last_update_success = False
        # The service's reason for waiting stays while the service answers.
        assert waiting.available
        assert not departure.available
        runtime.client._connected = False
        assert not waiting.available
        runtime.client._connected = True
        runtime.coordinator.last_update_success = True
        # A car without departure planning shows no plan, and the time is the
        # service's own again.
        runtime.departure_observer = None
        assert not set(PLAN) & waiting.extra_state_attributes.keys()
        assert departure.available
        assert departure.native_value is None
        assert not transport.writes
        await runtime.client.async_close()

    asyncio.run(run())


def test_the_services_summary_is_one_sensor_with_its_details(monkeypatch):
    async def run():
        hass, entry, transport = await build_runtime()
        start, deadline = datetime(2026, 11, 10, 3, tzinfo=UTC), datetime(2026, 11, 10, 7, tzinfo=UTC)
        summary = [Operation(
            "departure_plan", "until_planned_start", "plan_start", start, "level", 80.0, deadline, "confirmed",
        )]
        monkeypatch.setattr(ServiceControl, "operation", property(lambda _self: summary[0]))
        _datetimes, sensors = await _entities(hass, entry)
        assert not {"operation_controller", "operation_next"} & sensors.keys()
        waiting = sensors["operation_waiting"]
        assert waiting.native_value == "until_planned_start"
        assert waiting.native_value in waiting.entity_description.options
        details = waiting.extra_state_attributes
        assert {key: details[key] for key in (
            "controller", "next_event", "next_at", "vehicle", "target", "target_value", "target_deadline",
        )} == {
            "controller": "departure_plan", "next_event": "plan_start", "next_at": start,
            "vehicle": "confirmed", "target": "level", "target_value": 80.0, "target_deadline": deadline,
        }
        summary[0] = Operation("direct", "none", "none", None, "none", None, None, "connected")
        details = waiting.extra_state_attributes
        assert (details["controller"], details["vehicle"]) == ("direct", "connected")
        assert not {"next_at", "target", "target_value", "target_deadline"} & details.keys()
        # A service without a summary leaves it unavailable.
        summary[0] = None
        assert (waiting.available, waiting.native_value, waiting.extra_state_attributes) == (False, None, None)
        assert not transport.writes
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_departure_time_respects_read_only_but_status_is_observable():
    async def run():
        hass, entry, transport = await build_runtime(read_only=True)
        _plan_departures(entry)
        entry.runtime_data.departure_observer = _departure_observer()
        datetimes, sensors = await _entities(hass, entry)
        assert not datetimes["departure_time"].available
        waiting = sensors["operation_waiting"]
        assert waiting.available
        assert waiting.extra_state_attributes["departure_plan"] == "charging"
        assert not transport.writes
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_calendar_and_vehicle_departure_follow_the_selected_cars_planning(monkeypatch):
    async def run():
        _hass, entry, transport = await build_runtime()
        runtime = entry.runtime_data
        _plan_departures(entry)
        runtime.departure_observer = observer = _departure_observer()
        await DEVICE_ACTIONS["use_vehicle_departure"](runtime)
        observer.async_use_vehicle_departure.assert_awaited_once()
        await DEVICE_ACTIONS["use_calendar_departure"](runtime)
        observer.async_use_next_calendar_event.assert_awaited_once()
        # A car without departure planning has no trip to take a time for.
        runtime.departure_observer = None
        for action in ("use_vehicle_departure", "use_calendar_departure"):
            with pytest.raises(ServiceValidationError) as error:
                await DEVICE_ACTIONS[action](runtime)
            assert error.value.translation_key == "departure_unavailable"
        observer.async_use_vehicle_departure.assert_awaited_once()
        assert not transport.writes
        await runtime.client.async_close()

    asyncio.run(run())


def test_the_time_entity_shows_the_guests_time_and_hands_edits_to_the_cars_observer(monkeypatch):
    async def run():
        hass, entry, _transport = await build_runtime()
        control = entry.runtime_data.control
        guest_time = datetime(2026, 11, 20, 9, tzinfo=UTC)
        remote = SimpleNamespace(trip=SimpleNamespace(deadline=guest_time))
        monkeypatch.setattr(ServiceControl, "departure", property(lambda _: remote))
        control.guest_active = lambda: True
        control.async_set_target_time = AsyncMock()
        entry.runtime_data.departure_observer = observer = _departure_observer()
        _plan_departures(entry)
        dates, _sensors = await _entities(hass, entry)
        entity = dates["departure_time"]
        assert entity.available
        assert entity.native_value == guest_time
        # Automations see that the time is a guest's, not the car's departure.
        assert entity.extra_state_attributes == {"guest": True}
        control.guest_active = lambda: False
        assert entity.extra_state_attributes == {"guest": False, "vehicle": None}
        entry.options.update(catalog())
        entry.runtime_data.vehicle_profile_id = A
        assert entity.extra_state_attributes == {"guest": False, "vehicle": "First car"}
        control.guest_active = lambda: True
        # The observer knows that a guest charges and sets the guest's time.
        await entity.async_set_value(guest_time)
        observer.async_set_deadline.assert_awaited_with(guest_time)
        await entity.async_clear_value()
        observer.async_set_deadline.assert_awaited_with(None)
        control.async_set_target_time.assert_not_awaited()
        # Neither a car's trip nor a plan of the service: nothing to show.
        entry.runtime_data.departure_observer = None
        monkeypatch.setattr(ServiceControl, "departure", property(lambda _: None))
        assert not entity.available
        await entry.runtime_data.client.async_close()
    asyncio.run(run())
