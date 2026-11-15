"""Departure planning is optional and disabled trips cannot return on restart."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
    DepartureModbusService,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.timed_goal_const import (
    CONF_TIMED_GOAL_ENABLED,
    CONF_TIMED_GOAL_RESET_PENDING,
    timed_goal_is_enabled,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import TOKEN, current_writes
from tests.test_service_reauth import add_entry
from tests.test_soc_goal_ha_lifecycle import _entry

SOC = "sensor.car_soc"
OPTIONS = {
    "control_mode": "master", "scan_interval_seconds": 300,
    "measurement_interval_seconds": 300, "current_limit": 16,
    "state_change_interval_seconds": 0, "phase_switch_grace_seconds": 0,
    "vehicle_soc_entity": SOC,
}


async def planning_service(tmp_path):
    """A service that plans departures, as the app runs it."""
    client = MennekesModbusClient(
        SerialConfig("/dev/fake"), client_factory=lambda _: FakeModbusClient(device_bank()),
    )
    service = DepartureModbusService(
        ServerConfig(client.config, TOKEN, read_only=False, current_cap=16), tmp_path / "intent.json", client=client,
    )
    await service._initialize()
    return service


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
                # One time entity serves the car's trip or, without one, the
                # service's own time; it exists either way.
                registry = er.async_get(hass)
                entity_id = registry.async_get_entity_id("datetime", DOMAIN, f"{SERIAL_NUMBER}_timed_departure")
                assert entity_id is not None
                deadline = datetime.now(UTC) + timedelta(hours=8)
                if enabled:
                    await entry.runtime_data.timed_goal.async_set_deadline(deadline)
                    assert entry.runtime_data.timed_goal.goal.deadline == deadline
                else:
                    with pytest.raises(ServiceValidationError) as error:
                        await hass.services.async_call(DOMAIN, "use_calendar_departure", {}, blocking=True)
                    assert error.value.translation_key == "timed_goal_unavailable"
                assert current_writes(transport) == []
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("reenabled", [False, True])
def test_disabled_planning_clears_the_trip_after_an_unreachable_unload(tmp_path, reenabled):
    async def run():
        service = await planning_service(tmp_path)
        hass = await _boot(tmp_path / "ha")
        try:
            await service.heartbeat.async_stop()
            async with TestServer(create_app(service)) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
                hass.config_entries.async_update_entry(entry, options={**OPTIONS, CONF_TIMED_GOAL_ENABLED: True})
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                await runtime.control.async_set_soc_goal(80, source=f"{SOC}|", soc=20)
                deadline = datetime.now(UTC) + timedelta(hours=8)
                await runtime.timed_goal.async_set_deadline(deadline)
                await hass.async_block_till_done()
                assert service.departure.trip.deadline == deadline
                # Closing the client makes the unload's withdrawal fail, while
                # the independent service keeps the trip.
                await runtime.client.async_close()
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert service.departure.trip.deadline == deadline
                hass.config_entries.async_update_entry(entry, options={
                    **OPTIONS, CONF_TIMED_GOAL_ENABLED: reenabled, CONF_TIMED_GOAL_RESET_PENDING: True,
                })
                if reenabled:
                    # A reconnect that cannot reach the app keeps the reset
                    # pending, so re-enabling never revives the old trip.
                    with patch.object(ServiceClient, "async_connect", side_effect=ServiceConnectionError("service_not_ready", "offline")):
                        assert not await hass.config_entries.async_setup(entry.entry_id)
                    assert entry.options[CONF_TIMED_GOAL_RESET_PENDING] is True
                    assert await hass.config_entries.async_reload(entry.entry_id)
                else:
                    assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                assert (entry.runtime_data.timed_goal is not None) is reenabled
                assert CONF_TIMED_GOAL_RESET_PENDING not in entry.options
                assert service.control.soc_goal.target_percent == 80
                if reenabled:
                    assert entry.runtime_data.timed_goal.goal.deadline is None
                # Neither does the service keep planning a trip nobody shows.
                assert service.departure.trip.deadline is None
                hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_TIMED_GOAL_ENABLED: True})
                await hass.async_block_till_done()
                assert entry.runtime_data.timed_goal.goal.deadline is None
                assert service.departure.trip.deadline is None
                assert service.control.soc_goal.target_percent == 80
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_a_failed_withdrawal_keeps_the_reset_for_the_next_setup(tmp_path):
    async def run():
        transport = FakeModbusClient(device_bank())
        with patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(entry, options={
                    **OPTIONS, CONF_TIMED_GOAL_ENABLED: False, CONF_TIMED_GOAL_RESET_PENDING: True,
                })
                withdraw = AsyncMock(side_effect=HomeAssistantError("offline"))
                with patch.object(ServiceControl, "async_set_departure_trip", withdraw):
                    assert not await hass.config_entries.async_setup(entry.entry_id)
                withdraw.assert_awaited_once_with(None, None, withdraw=True)
                assert entry.state is ConfigEntryState.SETUP_RETRY
                assert entry.options[CONF_TIMED_GOAL_RESET_PENDING] is True
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert CONF_TIMED_GOAL_RESET_PENDING not in entry.options
                assert entry.runtime_data.control.departure.trip.deadline is None
                assert current_writes(transport) == []
            finally:
                await hass.async_stop()

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
                hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
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
