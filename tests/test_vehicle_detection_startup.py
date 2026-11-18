"""Automatic vehicle selection must be revalidated across startup awaits."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import repairs
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass, capture_timers
from tests.test_vehicle_detection import detection_catalog


def setup_case(tmp_path, monkeypatch):
    real = HomeAssistant(str(tmp_path / "real"))
    hass = FakeHass(str(tmp_path / "fake"))
    hass.states = real.states
    options = detection_catalog()
    options["control_mode"] = "master"
    for profile in options["vehicle_profiles"].values():
        profile["options"]["charge_loss_source"] = "disabled"
        profile["options"]["timed_goal_enabled"] = True
    entry = FakeConfigEntry(options=options)
    transport = FakeModbusClient(device_bank())
    # The service embedded for this direct entry reaches the simulated wallbox.
    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
        lambda *_args, **_kwargs: transport,
    )
    monkeypatch.setattr(repairs.ir, "async_create_issue", lambda *a, **k: None)
    monkeypatch.setattr(repairs.ir, "async_delete_issue", lambda *a, **k: None)
    # The fake HA has no event bus to watch the vehicle's reports on.
    monkeypatch.setattr(
        integration, "VehicleBinding",
        lambda *_: Mock(async_start=AsyncMock(), stop=Mock(), resting=False),
    )
    return real, hass, entry, transport


def connected_vehicle(hass, *, first):
    for name, connected in (("first", first), ("second", not first)):
        hass.states.async_set(f"binary_sensor.{name}_plug", "on" if connected else "off")
        hass.states.async_set(f"device_tracker.{name}", "home")


@pytest.mark.parametrize("stage", ["connect", "first_refresh", "soc_start"])
def test_changed_vehicle_aborts_setup_before_old_trip_can_start(tmp_path, monkeypatch, stage):
    async def run():
        real, hass, entry, transport = setup_case(tmp_path, monkeypatch)
        timers = capture_timers(monkeypatch, integration.embedded)
        connected_vehicle(hass, first=True)
        original_connect = transport.connect
        original_refresh = integration.ServiceCoordinator.async_config_entry_first_refresh

        async def connect():
            result = await original_connect()
            if stage == "connect":
                connected_vehicle(hass, first=False)
            return result

        async def refresh(coordinator):
            await original_refresh(coordinator)
            if stage == "first_refresh":
                connected_vehicle(hass, first=False)

        async def start_soc():
            if stage == "soc_start":
                connected_vehicle(hass, first=False)

        vehicle = Mock(async_start=AsyncMock(side_effect=start_soc), async_stop=AsyncMock())
        timed = Mock()
        monkeypatch.setattr(transport, "connect", connect)
        monkeypatch.setattr(integration.ServiceCoordinator, "async_config_entry_first_refresh", refresh)
        monkeypatch.setattr(integration, "VehicleSocObserver", lambda *_args, **_kwargs: vehicle)
        monkeypatch.setattr(integration, "TimedGoalObserver", timed)
        try:
            with pytest.raises(ConfigEntryNotReady):
                await integration.async_setup_entry(hass, entry)
            assert entry.options["active_vehicle_profile"] is None
            assert entry.options["timed_goal_reset_pending"] is True
            timed.assert_not_called()
            if stage == "soc_start":
                vehicle.async_start.assert_awaited_once()
                vehicle.async_stop.assert_awaited_once()
            else:
                vehicle.async_start.assert_not_awaited()
            # The wallbox is fine: its service and heartbeat stay for the
            # retry, and stop only when none comes.
            assert transport.connected
            ((seconds, check, waiting),) = timers
            assert (seconds, waiting) == (30, True)
            # Home Assistant still plans its retry: the service stays.
            entry.state = ConfigEntryState.SETUP_RETRY
            timers.clear()
            await check(None)
            assert transport.close_calls == 0
            ((seconds, check, waiting),) = timers
            # No retry is coming any more.
            entry.state = ConfigEntryState.NOT_LOADED
            await check(None)
            assert not transport.connected
        finally:
            await real.async_stop()

    asyncio.run(run())
