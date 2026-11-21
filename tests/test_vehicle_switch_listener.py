"""Options applied in place must not hide a vehicle change in the same update."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components import mennekes_amtron_rtu as integration

TARIFF = {"module": "module_2", "regular_network": 0.1, "price_includes_network": True}


def _owners(hass):
    """The real cost and Plug & Charge owners behind the entry's listener."""
    from custom_components.mennekes_amtron_rtu.cost_observer import ChargingCostObserver
    from custom_components.mennekes_amtron_rtu.plug_charge import PlugChargeObserver
    from custom_components.mennekes_amtron_rtu.vehicle_profiles import without_selection

    options = {"charging_cost_source": "fixed", "charging_price": 0.3, "virtual_plug_charge": False}
    runtime = SimpleNamespace(
        identity=SimpleNamespace(serial_number="123", layout_version=0x0103),
        client=SimpleNamespace(snapshot={}), entry_data={}, vehicle_profile_id=None,
        vehicle_base_options=without_selection(options),
        vehicle_selection=dict.fromkeys(
            ("active_vehicle_profile", "vehicle_profile_selected_at", "vehicle_auto_selection"),
        ),
    )
    entry = SimpleNamespace(entry_id="entry", data={}, options=options, runtime_data=runtime)
    runtime.charging_cost = ChargingCostObserver(hass, entry)
    gate = PlugChargeObserver.__new__(PlugChargeObserver)
    gate._applied_enabled = None
    runtime.plug_charge = gate
    return entry, runtime


def _switch(entry, enabled: bool) -> None:
    """What the Plug & Charge switch does once the service applied it."""
    entry.runtime_data.plug_charge._applied_enabled = enabled
    entry.options = {**entry.options, "virtual_plug_charge": enabled}


@pytest.mark.parametrize("order", ["switch_first", "tariff_first"])
def test_tariff_and_switch_changes_are_applied_in_place_in_any_order(tmp_path, order):
    from homeassistant.core import HomeAssistant

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.country, hass.config.currency = "DE", "EUR"
        entry, runtime = _owners(hass)
        cost = runtime.charging_cost
        reload = AsyncMock()
        listener = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_reload=reload))

        async def changed():
            await integration._async_reload_entry_locked(listener, entry)
            reload.assert_not_awaited()
            assert runtime.vehicle_base_options == entry.options

        steps = [
            lambda: _switch(entry, True),
            lambda: setattr(entry, "options", {**entry.options, "charging_network_tariff": TARIFF}),
        ]
        for step in steps if order == "switch_first" else reversed(steps):
            step()
            await changed()
        assert cost._tariff.module == "module_2"
        # Taking the tariff away again is a change like any other: it must
        # not be taken for the switch that was applied earlier.
        entry.options = {key: value for key, value in entry.options.items() if key != "charging_network_tariff"}
        await changed()
        assert cost._tariff.module == "none"
        _switch(entry, False)
        await changed()
        await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["other_option", "switch_by_options", "connection"])
def test_anything_else_reloads_the_entry(tmp_path, monkeypatch, change):
    from homeassistant.core import HomeAssistant

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.country, hass.config.currency = "DE", "EUR"
        entry, runtime = _owners(hass)
        entry.state = None
        runtime.retain_cost_on_unload = False
        reload = AsyncMock()
        monkeypatch.setattr(integration, "_async_park_trip", AsyncMock())
        monkeypatch.setattr(integration, "mark_reloading", lambda *_args: None)
        monkeypatch.setattr(integration, "async_release", AsyncMock())
        listener = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_reload=reload))
        entry.options = {**entry.options, "charging_network_tariff": TARIFF}
        if change == "other_option":
            entry.options = {**entry.options, "charging_price": 0.4}
        elif change == "switch_by_options":
            # Saved in the options, not applied by the switch.
            entry.options = {**entry.options, "virtual_plug_charge": True}
        else:
            entry.data = {"server_token": "replacement"}
        await integration._async_reload_entry_locked(listener, entry)
        reload.assert_awaited_once()
        # Nothing was taken over on the way to the reload.
        assert runtime.charging_cost._tariff.module == "none"
        assert "charging_network_tariff" not in runtime.vehicle_base_options
        await hass.async_stop()

    asyncio.run(run())


def test_an_in_place_change_still_switches_the_vehicle(monkeypatch, tmp_path):
    from homeassistant.core import HomeAssistant

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.country, hass.config.currency = "DE", "EUR"
        entry, runtime = _owners(hass)
        switch = AsyncMock(return_value=True)
        monkeypatch.setattr(integration, "_async_switch_vehicle", switch)
        listener = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_reload=AsyncMock()))
        entry.options = {**entry.options, "charging_network_tariff": TARIFF, "active_vehicle_profile": "b" * 32}
        await integration._async_reload_entry_locked(listener, entry)
        # The base settings follow the in-place change, then the vehicle switch
        # runs; neither reloads the entry.
        assert runtime.vehicle_base_options["charging_network_tariff"] == TARIFF
        switch.assert_awaited_once_with(listener, entry)
        listener.config_entries.async_reload.assert_not_awaited()
        await hass.async_stop()

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
            client=SimpleNamespace(snapshot={}), entry_data={}, vehicle_base_options=without_selection(options),
            vehicle_selection={"previous": True}, vehicle_profile_id=None,
            coordinator=SimpleNamespace(retry_soc_goal=None), vehicle_switching=False,
        )
        entry = SimpleNamespace(
            entry_id="entry", data={}, options=options, runtime_data=runtime,
            state=ConfigEntryState(state),
        )
        reloaded = []

        async def reload(_hass, reloading, *, force):
            # Forced: no option changed, and the embedded service is kept.
            assert force
            reloaded.append(reloading.entry_id)

        monkeypatch.setattr(integration, "async_reload_entry", reload)
        hass = SimpleNamespace(
            is_stopping=stopping,
            async_create_task=lambda target, _name: asyncio.create_task(target),
        )
        monkeypatch.setattr(integration, "_async_park_trip", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await integration._async_switch_vehicle(hass, entry)
        await asyncio.sleep(0)
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
            loaded_platforms=[], retain_cost_on_unload=False,
            vehicle_profile_id="a" * 32,
            vehicle_detection=None, vehicle_binding=None,
            charge_watch_unsubscribe=None,
            plug_charge=None, coordinator=SimpleNamespace(), charge_loss=None, charging_cost=None,
            cost_goal=None, departure_observer=timed, vehicle_soc=None,
            client=SimpleNamespace(async_close=AsyncMock()), vehicle_switch=None,
        )
        options = {**detection_catalog(), "active_vehicle_profile": selected}
        options["vehicle_profiles"]["a" * 32]["options"]["departure_enabled"] = planning
        entry = SimpleNamespace(entry_id="entry", options=options, runtime_data=runtime, disabled_by=None)
        handoff = SimpleNamespace()
        hass = SimpleNamespace(
            data={f"{integration.DOMAIN}_reload_clients": {"entry": handoff}},
            config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True)),
        )
        # No embedded service runs behind this entry.
        monkeypatch.setattr(integration, "async_release", AsyncMock())
        assert await integration.async_unload_entry(hass, entry)
        timed.async_stop.assert_awaited_once_with(withdraw=withdraw)

    asyncio.run(run())


def test_a_forced_reload_rebuilds_the_runtime_although_no_option_changed(monkeypatch):
    async def run():
        runtime = SimpleNamespace(
            plug_charge=None, charging_cost=None, entry_data={"other": 1}, identity=None,
            vehicle_base_options={},
            retain_cost_on_unload=False,
        )
        entry = SimpleNamespace(entry_id="entry", data={}, options={}, runtime_data=runtime, state=None)
        switch = AsyncMock(return_value=True)
        monkeypatch.setattr(integration, "_async_switch_vehicle", switch)
        monkeypatch.setattr(integration, "async_release", AsyncMock())
        marks = []
        monkeypatch.setattr(integration, "mark_reloading", lambda _hass, _id, on: marks.append(on))
        reload = AsyncMock()
        hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_reload=reload))
        # An option reload that only switched the car in place reloads nothing.
        await integration.async_reload_entry(hass, entry)
        reload.assert_not_awaited()
        await integration.async_reload_entry(hass, entry, force=True)
        reload.assert_awaited_once_with("entry")
        switch.assert_awaited_once()
        # The embedded service is kept through it.
        assert marks == [True, False]

    asyncio.run(run())


def _listening(tmp_path, monkeypatch, body):
    """Run body with the real owners and a listener whose reload is counted."""
    from homeassistant.core import HomeAssistant

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.country, hass.config.currency = "DE", "EUR"
        try:
            entry, runtime = _owners(hass)
            entry.state = None
            runtime.retain_cost_on_unload = False
            monkeypatch.setattr(integration, "_async_park_trip", AsyncMock())
            monkeypatch.setattr(integration, "mark_reloading", lambda *_args: None)
            monkeypatch.setattr(integration, "async_release", AsyncMock())
            reload = AsyncMock()
            listener = SimpleNamespace(data={}, config_entries=SimpleNamespace(async_reload=reload))
            await body(entry, runtime, listener, reload)
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_both_in_place_changes_in_one_update_are_applied_once(tmp_path, monkeypatch):
    async def body(entry, runtime, listener, reload):
        cost = runtime.charging_cost
        applied = []
        real = cost.apply_tariff_update
        cost.apply_tariff_update = lambda: applied.append(1) or real()
        _switch(entry, True)
        entry.options = {**entry.options, "charging_network_tariff": TARIFF}
        # Each of the two saves queued its own notification.
        await integration._async_reload_entry_locked(listener, entry)
        await integration._async_reload_entry_locked(listener, entry)
        reload.assert_not_awaited()
        assert applied == [1]
        assert cost._tariff.module == "module_2"

    _listening(tmp_path, monkeypatch, body)


def test_a_tariff_that_cannot_be_read_reloads_and_records_nothing(tmp_path, monkeypatch):
    async def body(entry, runtime, listener, reload):
        _switch(entry, True)
        entry.options = {**entry.options, "charging_network_tariff": {"module": "bogus"}}
        await integration._async_reload_entry_locked(listener, entry)
        reload.assert_awaited_once()
        assert runtime.vehicle_base_options["virtual_plug_charge"] is False
        assert "charging_network_tariff" not in runtime.vehicle_base_options

    _listening(tmp_path, monkeypatch, body)


@pytest.mark.parametrize("missing", ["cost", "gate"])
def test_a_change_without_its_owner_reloads(tmp_path, monkeypatch, missing):
    async def body(entry, runtime, listener, reload):
        if missing == "cost":
            runtime.charging_cost = None
            entry.options = {**entry.options, "charging_network_tariff": TARIFF}
        else:
            runtime.plug_charge = None
            entry.options = {**entry.options, "virtual_plug_charge": True}
        await integration._async_reload_entry_locked(listener, entry)
        reload.assert_awaited_once()

    _listening(tmp_path, monkeypatch, body)


def test_a_vehicle_switch_that_fails_after_an_applied_tariff_reloads_with_it(tmp_path, monkeypatch):
    async def body(entry, runtime, listener, reload):
        cost = runtime.charging_cost
        monkeypatch.setattr(integration, "_async_stop_vehicle", AsyncMock(side_effect=[RuntimeError("x"), None]))
        runtime.vehicle_switching = False
        entry.options = {**entry.options, "charging_network_tariff": TARIFF, "active_vehicle_profile": "b" * 32}
        await integration._async_reload_entry_locked(listener, entry)
        reload.assert_awaited_once()
        assert cost._tariff.module == "module_2"
        assert cost.retain_for_reload(entry)

    _listening(tmp_path, monkeypatch, body)


def test_options_saved_back_against_the_applied_switch_reload(tmp_path, monkeypatch):
    async def body(entry, runtime, listener, reload):
        # The switch applied "on" at the service; before its notification ran,
        # the options were saved with "off" again. Nothing differs from the
        # record, but the service runs what the options do not say.
        _switch(entry, True)
        entry.options = {**entry.options, "virtual_plug_charge": False}
        await integration._async_reload_entry_locked(listener, entry)
        reload.assert_awaited_once()

    _listening(tmp_path, monkeypatch, body)


def test_an_unload_cancels_and_awaits_a_running_vehicle_switch(monkeypatch):
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.mennekes_amtron_rtu.vehicle_profiles import without_selection

    async def run():
        options = {"active_vehicle_profile": "b" * 32}
        runtime = SimpleNamespace(
            client=SimpleNamespace(snapshot={}, async_close=AsyncMock()), entry_data={},
            vehicle_base_options=without_selection(options),
            vehicle_selection={"previous": True}, vehicle_profile_id=None,
            coordinator=SimpleNamespace(retry_soc_goal=None), vehicle_switching=False,
            vehicle_switch=None, loaded_platforms=[], retain_cost_on_unload=False,
            vehicle_detection=None, vehicle_binding=None, charge_watch_unsubscribe=None,
            plug_charge=None, charge_loss=None, charging_cost=None, cost_goal=None,
            departure_observer=None, vehicle_soc=None,
        )
        entry = SimpleNamespace(
            entry_id="entry", data={}, options=options, runtime_data=runtime,
            state=ConfigEntryState.UNLOAD_IN_PROGRESS,
        )
        parked = asyncio.Event()
        order = []

        async def park(_hass, _entry):
            parked.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                order.append("switch cancelled")
                raise

        async def unload_platforms(_entry, _platforms):
            order.append("platforms unloaded")
            return True

        monkeypatch.setattr(integration, "_async_park_trip", park)
        monkeypatch.setattr(integration, "async_release", AsyncMock())
        hass = SimpleNamespace(
            is_stopping=False, data={},
            async_create_task=lambda target, _name: asyncio.create_task(target),
            config_entries=SimpleNamespace(async_unload_platforms=unload_platforms),
        )
        switching = asyncio.create_task(integration._async_switch_vehicle(hass, entry))
        await parked.wait()
        assert await integration.async_unload_entry(hass, entry)
        # The switch ended before anything of the runtime was stopped.
        assert order == ["switch cancelled", "platforms unloaded"]
        with pytest.raises(asyncio.CancelledError):
            await switching
        assert runtime.vehicle_switch is None

    asyncio.run(run())


def test_an_owner_that_fails_to_stop_does_not_keep_the_others_running():
    async def run():
        failed = SimpleNamespace(async_stop=AsyncMock(side_effect=RuntimeError("broken")))
        plan = SimpleNamespace(async_stop=AsyncMock())
        soc = SimpleNamespace(async_stop=AsyncMock())
        client = SimpleNamespace(async_close=AsyncMock())
        runtime = SimpleNamespace(
            vehicle_detection=None, vehicle_binding=None, charge_watch_unsubscribe=None,
            plug_charge=failed, coordinator=SimpleNamespace(), charge_loss=None, charging_cost=None,
            cost_goal=None, departure_observer=plan, vehicle_soc=soc, client=client,
        )
        with pytest.raises(RuntimeError, match="broken"):
            await integration._async_stop_runtime(runtime)
        plan.async_stop.assert_awaited_once_with(withdraw=False)
        soc.async_stop.assert_awaited_once()
        client.async_close.assert_awaited_once()

    asyncio.run(run())


def test_a_cancelled_vehicle_stop_still_stops_the_cars_other_observers():
    async def run():
        withdrawing = asyncio.Event()

        async def withdraw(*, withdraw):
            withdrawing.set()
            await asyncio.Event().wait()

        plan = SimpleNamespace(async_stop=withdraw)
        soc = SimpleNamespace(async_stop=AsyncMock())
        loss = SimpleNamespace(async_stop=AsyncMock())
        runtime = SimpleNamespace(
            vehicle_profile_id="a" * 32, departure_observer=plan, vehicle_soc=soc,
            charge_loss=loss, vehicle_telemetry=None,
        )
        stopping = asyncio.create_task(integration._async_stop_vehicle(runtime, withdraw=True))
        await withdrawing.wait()
        # An unload cancels the switch while the old plan is withdrawn.
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        soc.async_stop.assert_awaited_once()
        loss.async_stop.assert_awaited_once()
        assert runtime.vehicle_soc is None
        assert runtime.charge_loss is None

    asyncio.run(run())
