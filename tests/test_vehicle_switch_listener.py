"""Options applied in place must not hide a vehicle change in the same update."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components import mennekes_amtron_rtu as integration


@pytest.mark.parametrize("applied_by", ["plug_charge", "charging_cost"])
def test_in_place_option_update_still_switches_the_vehicle(monkeypatch, applied_by):
    async def run():
        runtime = SimpleNamespace(
            plug_charge=SimpleNamespace(options_applied=lambda: applied_by == "plug_charge"),
            charging_cost=SimpleNamespace(apply_tariff_only_update=lambda: applied_by == "charging_cost"),
            vehicle_base_options={},
        )
        entry = SimpleNamespace(
            entry_id="entry", data={},
            options={"virtual_plug_charge": True, "active_vehicle_profile": "b" * 32},
            runtime_data=runtime,
        )
        switch = AsyncMock(return_value=True)
        monkeypatch.setattr(integration, "_async_switch_vehicle", switch)
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=AsyncMock()))
        await integration._async_reload_entry_locked(hass, entry)
        # The base settings follow the in-place change, then the vehicle switch
        # runs; neither reloads the entry.
        assert runtime.vehicle_base_options == {"virtual_plug_charge": True}
        switch.assert_awaited_once_with(hass, entry)
        hass.config_entries.async_reload.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(("state", "stopping", "reloads"), [
    ("loaded", False, True), ("loaded", True, False), ("unload_in_progress", False, False),
])
def test_a_cancelled_switch_reloads_only_a_running_entry(monkeypatch, state, stopping, reloads):
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.mennekes_amtron_rtu.vehicle_profiles import without_selection

    async def run():
        options = {"active_vehicle_profile": "b" * 32}
        runtime = SimpleNamespace(
            entry_data={}, vehicle_base_options=without_selection(options),
            vehicle_selection={"previous": True},
            coordinator=SimpleNamespace(retry_soc_goal=None), vehicle_switching=False,
        )
        entry = SimpleNamespace(
            entry_id="entry", data={}, options=options, runtime_data=runtime,
            state=ConfigEntryState(state),
        )
        reloaded = []
        hass = SimpleNamespace(
            is_stopping=stopping,
            config_entries=SimpleNamespace(async_schedule_reload=reloaded.append),
        )
        monkeypatch.setattr(integration, "_async_park_trip", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await integration._async_switch_vehicle(hass, entry)
        assert reloaded == (["entry"] if reloads else [])
        assert runtime.vehicle_switching is False

    asyncio.run(run())


@pytest.mark.parametrize(("selected", "planning", "withdraw"), [
    ("a" * 32, True, False), ("b" * 32, True, True),
    # Switching the car's departure planning off withdraws the app's plan too.
    ("a" * 32, False, True),
])
def test_a_reload_with_another_car_ends_the_old_plans_pause(monkeypatch, selected, planning, withdraw):
    from tests.test_vehicle_detection import detection_catalog

    async def run():
        timed = SimpleNamespace(async_stop=AsyncMock())
        runtime = SimpleNamespace(
            loaded_platforms=[], retain_client_on_unload=False, retain_cost_on_unload=False,
            retain_attribution_on_unload=True, vehicle_profile_id="a" * 32,
            vehicle_detection=None, vehicle_binding=None, loss_attribution_unsubscribe=None,
            plug_charge=None, coordinator=SimpleNamespace(), charge_loss=None, charging_cost=None,
            sessions=None, timed_goal=timed, vehicle_soc=None, soc_goal=None, goal=None,
            recovery=None, heartbeat=None,
            control=SimpleNamespace(async_shutdown=AsyncMock()),
            client=SimpleNamespace(async_close=AsyncMock()),
        )
        options = {**detection_catalog(), "active_vehicle_profile": selected}
        options["vehicle_profiles"]["a" * 32]["options"]["timed_goal_enabled"] = planning
        entry = SimpleNamespace(entry_id="entry", options=options, runtime_data=runtime)
        handoff = SimpleNamespace()
        hass = SimpleNamespace(
            data={f"{integration.DOMAIN}_reload_clients": {"entry": handoff}},
            config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True)),
        )
        assert await integration.async_unload_entry(hass, entry)
        timed.async_stop.assert_awaited_once_with(withdraw=withdraw)

    asyncio.run(run())
