"""Departure planning is optional and disabled trips cannot return on restart."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.timed_goal_const import (
    CONF_TIMED_GOAL_ENABLED,
    CONF_TIMED_GOAL_RESET_PENDING,
    timed_goal_is_enabled,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes, service_fixture
from tests.test_service_reauth import add_entry
from tests.test_soc_goal_ha_lifecycle import _entry

SOC = "sensor.car_soc"
OPTIONS = {
    "control_mode": "master", "scan_interval_seconds": 300,
    "measurement_interval_seconds": 300, "current_limit": 16,
    "state_change_interval_seconds": 0, "phase_switch_grace_seconds": 0,
    "vehicle_soc_entity": SOC,
}


@pytest.mark.parametrize(("options", "enabled"), [
    ({}, False), ({"vehicle_soc_entity": SOC}, False),
    ({CONF_TIMED_GOAL_ENABLED: True}, True),
    ({"timed_capacity_kwh": 80}, False),
    ({"timed_capacity_entity": "sensor.capacity"}, False),
    ({"timed_departure_entity": "input_datetime.trip"}, False),
    ({"timed_calendar_entity": "calendar.trip"}, False),
    ({"timed_bavarian_device": "old-bmw"}, False),
    ({"timed_vehicle_provider": "bavariandata", "timed_vehicle_device": "car"}, False),
    ({"timed_bavarian_device": "old-bmw", "timed_vehicle_provider": "none"}, False),
    ({"timed_vehicle_device": "car"}, False),
    ({"timed_vehicle_provider": "bavariandata"}, False),
    ({CONF_TIMED_GOAL_ENABLED: False, "timed_capacity_kwh": 80}, False),
    ({CONF_TIMED_GOAL_ENABLED: False, "timed_bavarian_device": "old-bmw"}, False),
])
def test_planning_requires_explicit_activation(options, enabled):
    assert timed_goal_is_enabled(options) is enabled


@pytest.mark.parametrize(("extra", "soc", "enabled"), [
    ({}, True, False),
    ({CONF_TIMED_GOAL_ENABLED: True}, False, False),
    ({CONF_TIMED_GOAL_ENABLED: True, "timed_calendar_entity": "calendar.trips"}, True, True),
    ({"timed_capacity_kwh": 80}, True, False),
    ({CONF_TIMED_GOAL_ENABLED: False, "timed_capacity_kwh": 80}, True, False),
])
def test_real_ha_only_adds_departure_entities_for_enabled_vehicle_planning(tmp_path, extra, soc, enabled):
    async def run():
        transport = FakeModbusClient(device_bank())
        with patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                options = {**OPTIONS, **extra}
                if not soc:
                    options.pop("vehicle_soc_entity")
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert (entry.runtime_data.timed_goal is not None) is enabled
                assert (entry.runtime_data.vehicle_soc is not None) is soc
                registry = er.async_get(hass)
                for domain, key in (("datetime", "timed_departure"), ("sensor", "timed_goal_status"), ("button", "timed_clear_departure")):
                    entity = registry.async_get_entity_id(domain, DOMAIN, f"{SERIAL_NUMBER}_{key}")
                    assert (entity is not None) is enabled
                if enabled:
                    deadline = datetime.now(UTC) + timedelta(hours=8)
                    await entry.runtime_data.timed_goal.async_set_deadline(deadline)
                    assert entry.runtime_data.timed_goal.goal.deadline == deadline
                    if "timed_calendar_entity" in extra:
                        assert registry.async_get_entity_id("button", DOMAIN, f"{SERIAL_NUMBER}_timed_next_calendar_event")
                assert current_writes(transport) == []
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("reenabled", [False, True])
def test_disabled_setup_reclaims_service_queue_after_unreachable_unload(tmp_path, manual, reenabled):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        gate = asyncio.Event()
        hass = await _boot(tmp_path / "ha")
        try:
            await service.heartbeat.async_stop()
            service.control._sleep = lambda _delay: gate.wait()
            async with TestServer(create_app(service)) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
                hass.config_entries.async_update_entry(entry, options={**OPTIONS, CONF_TIMED_GOAL_ENABLED: True})
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                await runtime.control.async_set_soc_goal(80, source=f"{SOC}|", soc=20)
                deadline = datetime.now(UTC) + timedelta(hours=8)
                await runtime.timed_goal.async_set_deadline(deadline)
                await runtime.control.async_pause_charging()
                await runtime.control.async_set_timed_current(10, deadline=deadline, owner=f"timed:{entry.entry_id}")
                if manual:
                    await runtime.control.async_set_charging_current(12)
                runtime.timed_goal._command_submitted = True
                pending = service.control._flush_task
                assert service.control.pending_action == "set_current"
                # Closing the client makes the observer's unload cancellation
                # fail, while the independent service retains its queue.
                await runtime.client.async_close()
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert service.control.pending_action == "set_current"
                hass.config_entries.async_update_entry(entry, options={
                    **OPTIONS, CONF_TIMED_GOAL_ENABLED: reenabled, CONF_TIMED_GOAL_RESET_PENDING: True,
                })
                if reenabled:
                    # A user can re-enable after an earlier failed disable.
                    # Deleting the trip must not consume the queue-cancel
                    # request if this reconnect attempt cannot reach the app.
                    with patch.object(RemoteClient, "async_connect", side_effect=ServiceConnectionError("service_not_ready", "offline")):
                        assert not await hass.config_entries.async_setup(entry.entry_id)
                    assert entry.options[CONF_TIMED_GOAL_RESET_PENDING] is True
                    assert service.control.pending_action == "set_current"
                if reenabled:
                    assert await hass.config_entries.async_reload(entry.entry_id)
                else:
                    assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                assert (entry.runtime_data.timed_goal is not None) is reenabled
                if reenabled:
                    assert entry.runtime_data.timed_goal.goal.deadline is None
                assert (service.control.pending_action is not None) is manual
                assert service.control.soc_goal.target_percent == 80
                assert service.control.pause_requested is True
                assert CONF_TIMED_GOAL_RESET_PENDING not in entry.options
                clock[0] += 301
                gate.set()
                await asyncio.wait_for(pending, 2)
                assert current_writes(transport) == ([1, 12] if manual else [1])
                hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_TIMED_GOAL_ENABLED: True})
                await hass.async_block_till_done()
                assert entry.runtime_data.timed_goal.goal.deadline is None
                assert service.control.soc_goal.target_percent == 80
                assert current_writes(transport) == ([1, 12] if manual else [1])
        finally:
            gate.set()
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("marked", [False, True])
def test_failed_trip_cleanup_stops_planner_and_survives_restart_and_reenable(tmp_path, marked):
    async def run():
        transport = FakeModbusClient(device_bank())
        config = tmp_path / "ha"
        with patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport):
            hass = await _boot(config)
            try:
                entry = await _entry(hass)
                hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
                hass.config_entries.async_update_entry(entry, options={**OPTIONS, CONF_TIMED_GOAL_ENABLED: True})
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                await runtime.control.async_set_soc_goal(80, source=f"{SOC}|", soc=20)
                runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await runtime.control.async_pause_charging()
                deadline = datetime.now(UTC) + timedelta(hours=8)
                await runtime.timed_goal.async_set_deadline(deadline)
                stored = runtime.timed_goal._store.path
                with patch.object(TimedGoalStore, "async_remove", side_effect=OSError("cannot delete")):
                    hass.config_entries.async_update_entry(entry, options={
                        **entry.options, CONF_TIMED_GOAL_ENABLED: False,
                        **({CONF_TIMED_GOAL_RESET_PENDING: True} if marked else {}),
                    })
                    await hass.async_block_till_done()
                    assert runtime.timed_goal._stopping
                    assert entry.state is ConfigEntryState.LOADED
                    assert entry.runtime_data.timed_goal is None
                    assert entry.runtime_data.heartbeat.running
                    assert stored.exists()
                    assert entry.options[CONF_TIMED_GOAL_RESET_PENDING] is True
            finally:
                await hass.async_stop()
            shutil.rmtree(config / "custom_components" / DOMAIN)
            hass = await _boot(config)
            try:
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                assert entry.options[CONF_TIMED_GOAL_RESET_PENDING] is True
                hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_TIMED_GOAL_ENABLED: True})
                with patch.object(TimedGoalStore, "async_remove", side_effect=OSError("still cannot delete")):
                    assert await hass.config_entries.async_setup(entry.entry_id)
                    await hass.async_block_till_done()
                    assert entry.runtime_data.timed_goal is None
                    assert entry.runtime_data.heartbeat.running
                    assert entry.runtime_data.control.soc_goal.target_percent == 80
                    assert entry.runtime_data.control.is_paused
                    assert stored.exists()
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                observer = entry.runtime_data.timed_goal
                assert observer.goal.deadline is None
                assert CONF_TIMED_GOAL_RESET_PENDING not in entry.options
                assert not stored.exists()
                await observer.async_set_deadline(deadline)
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert entry.runtime_data.timed_goal.goal.deadline == deadline
                assert current_writes(transport) == [1]
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
