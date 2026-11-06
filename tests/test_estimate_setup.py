"""Optional owners follow setup, partial failure, remote takeover and removal."""

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
from custom_components.mennekes_amtron_rtu.device.energy_goal import EnergyGoal
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.goal_store import (
    GoalStore,
    ModelStore,
    SessionHistoryStore,
    SocGoalStore,
)
from custom_components.mennekes_amtron_rtu.loss_store import LossStore
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_charging_session import complete
from tests.test_init import _patch_client, _patch_issues
from tests.test_modbus_service import TOKEN, service_fixture


def owners(monkeypatch, entry, *, failure=None, remote=False):
    events = []
    instances = {}

    def install(name, startup, shutdown):
        def factory(*_args, **kwargs):
            if name == "cost":
                assert kwargs["remote"] is remote

            async def start():
                events.append(f"{name}.start")
                if name == "loss":
                    assert entry.runtime_data.coordinator.data is not None
                if failure == name:
                    raise RuntimeError(f"{name} setup failed")

            async def observe(data):
                events.append(f"{name}.observe")
                if data is not None:
                    if name == "session":
                        data.session_summary_supported = True
                    else:
                        assert data.session_summary_supported

            async def stop():
                assert entry.runtime_data.coordinator.observe_session is None
                events.append(f"{name}.stop")

            instance = SimpleNamespace(
                **{startup: AsyncMock(side_effect=start), shutdown: AsyncMock(side_effect=stop)},
                async_observe=AsyncMock(side_effect=observe),
            )
            instances[name] = instance
            return instance

        return factory

    monkeypatch.setattr(integration, "SessionObserver", install("session", "async_setup", "async_close"))
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
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        events, instances = owners(monkeypatch, entry)
        assert await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        assert runtime.sessions is instances["session"]
        assert (runtime.charge_loss is not None) is loss
        assert (runtime.charging_cost is not None) is cost
        assert events[0] == "session.start"
        assert "session.observe" in events
        if cost:
            assert events.index("cost.start") < events.index("session.observe") < events.index("cost.observe")
        if loss:
            assert events.index("session.observe") < events.index("loss.start")
            assert Platform.BUTTON in runtime.loaded_platforms
        callback = runtime.coordinator.observe_session
        await callback(None)
        instances["session"].async_observe.assert_awaited_with(None)
        if cost:
            instances["cost"].async_observe.assert_awaited_with(None)
        hass.config_entries.unload_result = False
        assert not await integration.async_unload_entry(hass, entry)
        assert runtime.coordinator.observe_session is callback
        assert not any(event.endswith(".stop") for event in events)
        hass.config_entries.unload_result = True
        assert await integration.async_unload_entry(hass, entry)
        assert runtime.coordinator.observe_session is None
        assert events[-1] == "session.stop"
        if loss:
            instances["loss"].async_stop.assert_awaited_once()
        if cost:
            instances["cost"].async_stop.assert_awaited_once()
        instances["session"].async_close.assert_awaited_once()
        assert transport.close_calls == 1
        assert not transport.writes

    asyncio.run(run())


def test_disabled_optional_features_create_no_owners_or_poll_callback(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry()
        _patch_client(monkeypatch, FakeModbusClient(device_bank()))
        _patch_issues(monkeypatch)
        events, instances = owners(monkeypatch, entry)
        assert await integration.async_setup_entry(hass, entry)
        assert entry.runtime_data.coordinator.observe_session is None
        assert not instances
        assert await integration.async_unload_entry(hass, entry)
        assert not events

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["session", "cost", "poll", "loss", "platform"])
def test_partial_setup_failure_closes_every_created_estimate_owner_and_transport(tmp_path, monkeypatch, failure):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry(options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
            CONF_CHARGE_LOSS_SOURCE: "sensor", CONF_CHARGING_COST_SOURCE: "fixed",
        })
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        _events, instances = owners(monkeypatch, entry, failure=failure)
        if failure == "poll":
            monkeypatch.setattr(
                integration.MennekesAmtronCoordinator, "async_config_entry_first_refresh",
                AsyncMock(side_effect=ConfigEntryNotReady("poll failed")),
            )
        elif failure == "platform":
            hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=RuntimeError("platform failed"))
        with pytest.raises((RuntimeError, ConfigEntryNotReady)):
            await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        assert runtime.coordinator.observe_session is None
        instances["session"].async_close.assert_awaited_once()
        for name in ("cost", "loss"):
            if name in instances:
                instances[name].async_stop.assert_awaited_once()
        if runtime.heartbeat is not None:
            assert not runtime.heartbeat.running
        assert not runtime.client.connected
        assert transport.close_calls == 1

    asyncio.run(run())


def test_entry_removal_removes_all_bounded_stores_and_only_owned_repairs(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        entry = FakeConfigEntry()
        stores = [
            (GoalStore(hass, entry.entry_id, "box"), EnergyGoal()),
            (SocGoalStore(hass, entry.entry_id, "box"), SocGoal()),
            (SessionHistoryStore(hass, entry.entry_id, "box"), complete()),
            (LossStore(hass, entry.entry_id, "box"), LossStatistics()),
            (ModelStore(hass, entry.entry_id, "box", kind="charging_cost", model=LastChargingCost), LastChargingCost()),
        ]
        for store, model in stores:
            await store.async_save(model)
            assert await asyncio.to_thread(store.path.exists)
        foreign = SessionHistoryStore(hass, "other-entry", "other-box")
        await foreign.async_save(complete())
        hass.data[f"{DOMAIN}_write_states"] = {entry.entry_id: object(), "other-entry": object()}
        deleted = []
        issues = [(DOMAIN, "entry-1_error"), (DOMAIN, "other-entry_error"), ("other-domain", "entry-1_error")]
        monkeypatch.setattr(integration.ir, "async_get", lambda _hass: SimpleNamespace(issues=issues))
        monkeypatch.setattr(integration.ir, "async_delete_issue", lambda _hass, domain, issue: deleted.append((domain, issue)))
        await integration.async_remove_entry(hass, entry)
        for store, _model in stores:
            assert not await asyncio.to_thread(store.path.exists)
        assert await asyncio.to_thread(foreign.path.exists)
        assert deleted == [(DOMAIN, "entry-1_error")]
        assert list(hass.data[f"{DOMAIN}_write_states"]) == ["other-entry"]

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_remote_takeover_uses_app_sessions_and_removes_obsolete_direct_state(tmp_path, monkeypatch, enabled):
    async def run():
        hass = FakeHass(str(tmp_path / "ha"))
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        entry = FakeConfigEntry(options={
            CONF_CHARGE_LOSS_SOURCE: "sensor" if enabled else "disabled",
            CONF_CHARGING_COST_SOURCE: "fixed" if enabled else "disabled",
        })
        history = SessionHistoryStore(hass, entry.entry_id, "ABC123456789")
        await history.async_save(complete())
        losses = LossStore(hass, entry.entry_id, "ABC123456789")
        await losses.async_save(LossStatistics())
        _patch_issues(monkeypatch)
        events, instances = owners(monkeypatch, entry, remote=True)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                entry.data = {"server_url": str(server.make_url("/")), "server_token": TOKEN}
                monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
                assert await integration.async_setup_entry(hass, entry)
                runtime = entry.runtime_data
                assert runtime.sessions is None
                assert "session" not in instances
                assert runtime.coordinator.data.session_summary_supported
                assert not await asyncio.to_thread(history.path.exists)
                assert await asyncio.to_thread(losses.path.exists)
                assert Platform.BUTTON in runtime.loaded_platforms
                if enabled:
                    assert events[:3] == ["cost.start", "cost.observe", "loss.start"]
                    await runtime.coordinator.observe_session(None)
                    instances["cost"].async_observe.assert_awaited_with(None)
                assert await integration.async_unload_entry(hass, entry)
                assert runtime.coordinator.observe_session is None
                if enabled:
                    instances["loss"].async_stop.assert_awaited_once()
                    instances["cost"].async_stop.assert_awaited_once()
        finally:
            await service.stop()

    asyncio.run(run())
