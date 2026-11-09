"""Automatic vehicle selection must be revalidated across startup awaits."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.vehicle_detection import (
    VehicleDetectionObserver,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_client, _patch_issues
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A


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
    _patch_client(monkeypatch, transport)
    _patch_issues(monkeypatch)
    monkeypatch.setattr(integration, "HeartbeatRecovery", lambda *_: Mock(start=Mock(), async_stop=AsyncMock()))
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
        connected_vehicle(hass, first=True)
        original_connect = transport.connect
        original_refresh = integration.MennekesAmtronCoordinator.async_config_entry_first_refresh

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
        monkeypatch.setattr(integration.MennekesAmtronCoordinator, "async_config_entry_first_refresh", refresh)
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
            assert not transport.connected
        finally:
            await real.async_stop()

    asyncio.run(run())


def test_old_reached_goal_is_not_retried_before_new_source_binding(tmp_path, monkeypatch):
    async def run():
        real, hass, entry, _transport = setup_case(tmp_path, monkeypatch)
        connected_vehicle(hass, first=False)
        old = SocGoal()
        old.configure_source(f"profile:{A}:sensor.first_soc|")
        old.set_target(80, 80)
        monkeypatch.setattr(integration.SocGoalStore, "async_load", AsyncMock(return_value=old))
        monkeypatch.setattr(integration.SocGoalStore, "async_save", AsyncMock())
        original_refresh = integration.MennekesAmtronCoordinator.async_config_entry_first_refresh
        hooks_during_first_refresh = []

        async def refresh(coordinator):
            hooks_during_first_refresh.append(coordinator.retry_soc_goal)
            await original_refresh(coordinator)
            await asyncio.sleep(0)

        sources = []

        def observer(_hass, control, _coordinator, source, *_args, **_kwargs):
            async def start():
                sources.append(source)
                await control.async_configure_soc(source)
            return Mock(async_start=AsyncMock(side_effect=start), async_stop=AsyncMock())

        pause = AsyncMock()
        monkeypatch.setattr(integration.MennekesAmtronCoordinator, "async_config_entry_first_refresh", refresh)
        monkeypatch.setattr(integration, "VehicleSocObserver", observer)
        monkeypatch.setattr(integration.AmtronControl, "async_pause_for_energy_goal", pause)
        monkeypatch.setattr(VehicleDetectionObserver, "start", lambda self: None)
        try:
            assert await integration.async_setup_entry(hass, entry)
            assert hooks_during_first_refresh == [None]
            assert sources == [None]
            assert entry.runtime_data.control.soc_goal.source is None
            assert not entry.runtime_data.control.soc_goal.stop_requested
            assert entry.runtime_data.coordinator.retry_soc_goal is not None
            entry.runtime_data.coordinator.retry_soc_goal()
            await asyncio.sleep(0)
            pause.assert_not_awaited()
        finally:
            if entry.runtime_data is not None:
                await integration.async_unload_entry(hass, entry)
            await real.async_stop()

    asyncio.run(run())
