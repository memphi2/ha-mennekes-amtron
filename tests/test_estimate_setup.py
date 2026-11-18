"""Optional owners follow setup, partial failure, the app and removal."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import repairs
from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
)
from custom_components.mennekes_amtron_rtu.charging_cost_const import (
    CONF_CHARGING_COST_SOURCE,
)
from custom_components.mennekes_amtron_rtu.cost_observer import LastChargingCost
from custom_components.mennekes_amtron_rtu.device.charge_loss import LossStatistics
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.embedded import state_directory
from custom_components.mennekes_amtron_rtu.goal_store import (
    DepartureIntent,
    ModelStore,
    TimedGoalStore,
)
from custom_components.mennekes_amtron_rtu.loss_store import LossStore
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass, heartbeat_running
from tests.test_modbus_service import TOKEN, service_fixture


def direct_wallbox(monkeypatch):
    """Let the service embedded for a direct entry reach a simulated wallbox."""
    transport = FakeModbusClient(device_bank())
    monkeypatch.setattr(
        "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
        lambda *_args, **_kwargs: transport,
    )
    monkeypatch.setattr(repairs.ir, "async_create_issue", lambda *a, **k: None)
    monkeypatch.setattr(repairs.ir, "async_delete_issue", lambda *a, **k: None)
    return transport


def owners(monkeypatch, entry, *, failure=None):
    events = []
    instances = {}

    def install(name, startup, shutdown):
        def factory(*_args, **_kwargs):
            async def start():
                events.append(f"{name}.start")
                if name == "loss":
                    assert entry.runtime_data.coordinator.data is not None
                if failure == name:
                    raise RuntimeError(f"{name} setup failed")

            async def observe(data):
                events.append(f"{name}.observe")

            async def stop():
                assert entry.runtime_data.coordinator.observe_estimates is None
                events.append(f"{name}.stop")

            instance = SimpleNamespace(
                **{startup: AsyncMock(side_effect=start), shutdown: AsyncMock(side_effect=stop)},
                async_observe=AsyncMock(side_effect=observe),
            )
            instances[name] = instance
            return instance

        return factory

    monkeypatch.setattr(integration, "ChargingCostObserver", install("cost", "async_start", "async_stop"))
    monkeypatch.setattr(integration, "ChargeLossObserver", install("loss", "async_start", "async_stop"))
    return events, instances


@pytest.mark.parametrize(("loss", "cost"), [(True, True), (True, False), (False, True)])
def test_direct_estimates_are_wired_before_first_poll_and_closed_after_successful_unload(tmp_path, monkeypatch, loss, cost):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry(options={
            CONF_CHARGE_LOSS_SOURCE: "sensor" if loss else "disabled",
            CONF_CHARGING_COST_SOURCE: "fixed" if cost else "disabled",
        })
        transport = direct_wallbox(monkeypatch)
        events, instances = owners(monkeypatch, entry)
        assert await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        assert (runtime.charge_loss is not None) is loss
        assert (runtime.charging_cost is not None) is cost
        if cost:
            assert events[:2] == ["cost.start", "cost.observe"]
        if loss:
            # The loss owner starts on the first poll's state, after costs.
            assert events[-1] == "loss.start"
        # Assigning a charge is an action: a read-only entry adds no buttons.
        assert Platform.BUTTON not in runtime.loaded_platforms
        callback = runtime.coordinator.observe_estimates
        assert callback is not None
        # A failed poll reaches the costs as None; losses need no poll.
        await callback(None)
        if cost:
            instances["cost"].async_observe.assert_awaited_with(None)
        hass.config_entries.unload_result = False
        assert not await integration.async_unload_entry(hass, entry)
        assert runtime.coordinator.observe_estimates is callback
        assert not any(event.endswith(".stop") for event in events)
        hass.config_entries.unload_result = True
        assert await integration.async_unload_entry(hass, entry)
        assert runtime.coordinator.observe_estimates is None
        if loss:
            instances["loss"].async_stop.assert_awaited_once()
        if cost:
            instances["cost"].async_stop.assert_awaited_once()
        assert transport.close_calls == 1
        assert not transport.writes

    asyncio.run(run())


def test_disabled_optional_features_create_no_owners_or_poll_callback(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry()
        direct_wallbox(monkeypatch)
        events, instances = owners(monkeypatch, entry)
        assert await integration.async_setup_entry(hass, entry)
        assert entry.runtime_data.coordinator.observe_estimates is None
        assert not instances
        assert await integration.async_unload_entry(hass, entry)
        assert not events

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["cost", "poll", "loss", "platform"])
def test_partial_setup_failure_closes_every_created_estimate_owner_and_transport(tmp_path, monkeypatch, failure):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry(options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
            CONF_CHARGE_LOSS_SOURCE: "sensor", CONF_CHARGING_COST_SOURCE: "fixed",
        })
        transport = direct_wallbox(monkeypatch)
        _events, instances = owners(monkeypatch, entry, failure=failure)
        if failure == "poll":
            monkeypatch.setattr(
                integration.ServiceCoordinator, "async_config_entry_first_refresh",
                AsyncMock(side_effect=ConfigEntryNotReady("poll failed")),
            )
        elif failure == "platform":
            hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=RuntimeError("platform failed"))
        with pytest.raises((RuntimeError, ConfigEntryNotReady)):
            await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        assert runtime.coordinator.observe_estimates is None
        instances["cost"].async_stop.assert_awaited_once()
        assert ("loss" in instances) is (failure in ("loss", "platform"))
        if "loss" in instances:
            instances["loss"].async_stop.assert_awaited_once()
        if runtime.heartbeat is not None:
            assert not heartbeat_running(runtime.heartbeat)
        assert not runtime.client.connected
        # The embedded service is stopped, not kept for a reload.
        assert not hass.data.get(f"{DOMAIN}_embedded")
        assert transport.close_calls == 1

    asyncio.run(run())


def test_entry_removal_removes_all_bounded_stores_and_only_owned_repairs(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry()
        trip = TimedGoal()
        trip.set(None, "manual")
        stores = [
            (LossStore(hass, entry.entry_id, "box"), LossStatistics()),
            (ModelStore(hass, entry.entry_id, "box", kind="charging_cost", model=LastChargingCost), LastChargingCost()),
            (TimedGoalStore(hass, entry.entry_id, "box"), DepartureIntent(trip)),
        ]
        for store, model in stores:
            await store.async_save(model)
            assert await asyncio.to_thread(store.path.exists)
        # The embedded service keeps its goals and sessions in its own directory.
        service_state = state_directory(hass, entry.entry_id)
        await asyncio.to_thread(service_state.mkdir, parents=True)
        await asyncio.to_thread((service_state / "intent.json").write_text, "{}")
        foreign = LossStore(hass, "other-entry", "other-box")
        await foreign.async_save(LossStatistics())
        deleted = []
        issues = [(DOMAIN, "entry-1_error"), (DOMAIN, "other-entry_error"), ("other-domain", "entry-1_error")]
        monkeypatch.setattr(integration.ir, "async_get", lambda _hass: SimpleNamespace(issues=issues))
        monkeypatch.setattr(integration.ir, "async_delete_issue", lambda _hass, domain, issue: deleted.append((domain, issue)))
        await integration.async_remove_entry(hass, entry)
        for store, _model in stores:
            assert not await asyncio.to_thread(store.path.exists)
        assert not await asyncio.to_thread(service_state.exists)
        assert await asyncio.to_thread(foreign.path.exists)
        assert deleted == [(DOMAIN, "entry-1_error")]

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_app_entry_estimates_follow_the_apps_sessions_and_keep_saved_losses(tmp_path, monkeypatch, enabled):
    async def run():
        hass = FakeHass(str(tmp_path / "ha"))
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        entry = FakeConfigEntry(options={
            CONF_CHARGE_LOSS_SOURCE: "sensor" if enabled else "disabled",
            CONF_CHARGING_COST_SOURCE: "fixed" if enabled else "disabled",
        })
        losses = LossStore(hass, entry.entry_id, "ABC123456789")
        await losses.async_save(LossStatistics())
        monkeypatch.setattr(repairs.ir, "async_create_issue", lambda *a, **k: None)
        monkeypatch.setattr(repairs.ir, "async_delete_issue", lambda *a, **k: None)
        events, instances = owners(monkeypatch, entry)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                entry.data = {"server_url": str(server.make_url("/")), "server_token": TOKEN}
                monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
                assert await integration.async_setup_entry(hass, entry)
                runtime = entry.runtime_data
                assert await asyncio.to_thread(losses.path.exists)
                assert Platform.BUTTON in runtime.loaded_platforms
                if enabled:
                    assert events[:3] == ["cost.start", "cost.observe", "loss.start"]
                    await runtime.coordinator.observe_estimates(None)
                    instances["cost"].async_observe.assert_awaited_with(None)
                else:
                    assert not instances
                assert await integration.async_unload_entry(hass, entry)
                assert runtime.coordinator.observe_estimates is None
                if enabled:
                    instances["loss"].async_stop.assert_awaited_once()
                    instances["cost"].async_stop.assert_awaited_once()
        finally:
            await service.stop()

    asyncio.run(run())
