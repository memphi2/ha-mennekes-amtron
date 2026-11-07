"""Real entry ownership cancels yearly imports and failed timed setup."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_CHARGING_PRICE,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
)
from custom_components.mennekes_amtron_rtu.options_flow import _network_tariff
from custom_components.mennekes_amtron_rtu.vehicle_soc_const import (
    CONF_VEHICLE_SOC_ENTITY,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_charging_cost_options import NETWORK
from tests.test_home_assistant_smoke import _boot
from tests.test_init import _patch_client, _patch_issues
from tests.test_soc_goal_ha_lifecycle import _entry


@pytest.mark.parametrize("origin", ["startup", "daily"])
def test_annual_import_starts_after_wallbox_setup_and_is_cancelled_on_unload(tmp_path, monkeypatch, origin):
    async def run():
        transport = FakeModbusClient(device_bank())
        started, cancelled = asyncio.Event(), asyncio.Event()
        callbacks = []
        unsubscribe = Mock(return_value=None)
        tasks = []

        async def refresh(_hass, entry):
            runtime = entry.runtime_data
            assert runtime.heartbeat.running
            assert runtime.coordinator.data is not None
            assert runtime.loaded_platforms
            tasks.append(asyncio.current_task())
            if origin == "daily" and len(tasks) == 1:
                return False
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        def daily(_hass, callback, **kwargs):
            assert kwargs == {"hour": 0, "minute": 0, "second": 5}
            callbacks.append(callback)
            return unsubscribe

        monkeypatch.setattr(integration, "async_refresh_tariff_year", refresh)
        monkeypatch.setattr(integration, "async_track_time_change", daily)
        with patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(entry, options={
                    CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
                    CONF_CHARGING_COST_SOURCE: "fixed",
                    CONF_CHARGING_PRICE: 0.3,
                    CONF_AUTO_TARIFF_REFRESH: True,
                    CONF_CHARGING_NETWORK_TARIFF: _network_tariff(
                        {**NETWORK, "tariff_year": datetime.now(ZoneInfo("Europe/Berlin")).year - 1},
                        module="module_3", timezone="Europe/Berlin",
                    ),
                })
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                assert len(callbacks) == 1
                if origin == "daily":
                    assert len(tasks) == 1
                    assert tasks[0].done()
                    callbacks[0](None)
                await asyncio.wait_for(started.wait(), 3)
                runtime = entry.runtime_data
                assert runtime.heartbeat.running
                assert await hass.config_entries.async_unload(entry.entry_id)
                await asyncio.wait_for(cancelled.wait(), 3)
                assert tasks[-1].cancelled()
                unsubscribe.assert_called_once()
                assert not runtime.heartbeat.running
                assert not runtime.client.connected
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("failure", ["timed", "timed_connection", "recovery"])
def test_partial_timed_setup_stops_already_started_soc_and_heartbeat_owners(tmp_path, monkeypatch, failure):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry(options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
            CONF_VEHICLE_SOC_ENTITY: "sensor.car_soc",
        })
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        vehicle = Mock(async_start=AsyncMock(), async_stop=AsyncMock())
        timed_error = {"timed": ValueError("invalid saved trip"), "timed_connection": HomeAssistantError("service offline")}.get(failure)
        timed = Mock(async_start=AsyncMock(side_effect=timed_error), async_stop=AsyncMock())
        recovery = Mock(start=Mock(side_effect=RuntimeError("recovery startup failed")), async_stop=AsyncMock())
        monkeypatch.setattr(integration, "VehicleSocObserver", lambda *_args: vehicle)
        monkeypatch.setattr(integration, "TimedGoalObserver", lambda *_args: timed)
        monkeypatch.setattr(integration, "HeartbeatRecovery", lambda *_args: recovery)
        with pytest.raises(RuntimeError if failure == "recovery" else ConfigEntryNotReady) as caught:
            await integration.async_setup_entry(hass, entry)
        if failure != "recovery":
            assert caught.value.translation_key == ("timed_goal_state_invalid" if failure == "timed" else "timed_goal_unavailable")
            recovery.async_stop.assert_not_awaited()
        else:
            recovery.async_stop.assert_awaited_once()
        vehicle.async_start.assert_awaited_once()
        vehicle.async_stop.assert_awaited_once()
        timed.async_start.assert_awaited_once()
        timed.async_stop.assert_awaited_once()
        runtime = entry.runtime_data
        assert not runtime.heartbeat.running
        assert not runtime.client.connected
        assert transport.close_calls == 1

    asyncio.run(run())
