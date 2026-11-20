"""Sustained heartbeat failures must reach Repairs without readable registers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mennekes_amtron_rtu import repair_health, repairs
from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.repair_issues import (
    ISSUE_EMS_HEARTBEAT_LOST,
    ISSUE_HEARTBEAT_UNAVAILABLE,
    ISSUE_SERVICE_UNAVAILABLE,
)
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceHeartbeat
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.ha_fakes import build_runtime, heartbeat_running
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_repairs import _patch
from tests.test_soc_goal_ha_lifecycle import _entry


async def _setup(monkeypatch, *, remote=False, read_only=False, healthy=True, error=0):
    registry = _patch(monkeypatch)
    hass, entry, transport = await build_runtime(
        read_only=read_only, bank=device_bank(error_code=error),
    )
    entry.state = ConfigEntryState.LOADED
    runtime = entry.runtime_data
    if remote:
        # No HTTP request or socket is used. The authenticated cache and update
        # callback are exactly what the real remote heartbeat observes.
        original_client = runtime.client
        client = ServiceClient(SimpleNamespace(), "http://app.invalid:8765", "test")
        client._connected = True
        client.snapshot = {
            "control": {"read_only": read_only},
            "heartbeat": {"healthy": healthy, "running": not read_only},
        }
        runtime.client = client
        entry.data = {**entry.data, "server_url": "http://app.invalid:8765"}
        await original_client.async_close()
    else:
        # The service's own report of its heartbeat, as every poll returns it.
        client = runtime.client
        client.snapshot["heartbeat"]["healthy"] = healthy
    heartbeat = ServiceHeartbeat(client)

    def update():
        heartbeat.update()
        if not client.connected:
            runtime.coordinator.async_set_update_error(UpdateFailed("app unavailable"))

    client.on_update = update
    runtime.heartbeat = heartbeat
    repairs.async_setup_repairs(hass, entry)
    monitor = entry.unloads[-1].__self__
    assert isinstance(monitor, repair_health.HeartbeatRepairMonitor)
    return hass, entry, transport, registry, monitor


def _set_healthy(entry, healthy):
    """Publish the heartbeat health the service reports."""
    client = entry.runtime_data.client
    client.snapshot["heartbeat"]["healthy"] = healthy
    client.on_update()


def _expire(monitor):
    """Advance just this grace timer without sleeping or touching other tasks."""
    assert monitor._timer is not None
    monitor._timer.cancel()
    monitor._expired()


def _issue(entry, key):
    return f"{entry.entry_id}_{key}"


def test_heartbeat_listener_reports_sustained_problem_without_a_polled_fault(monkeypatch):
    async def run():
        hass, entry, transport, registry, monitor = await _setup(monkeypatch)
        runtime = entry.runtime_data
        _set_healthy(entry, False)
        timer = monitor._timer
        assert timer is not None
        assert timer.when() - hass.loop.time() == pytest.approx(60, abs=0.1)
        assert registry.created == {}
        assert runtime.coordinator.current_data().get("error_code") == 0
        for _ in range(5):
            runtime.coordinator.async_update_listeners()
            assert monitor._timer is timer  # Polling cannot keep postponing notice.
        _expire(monitor)
        issue_id = _issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE)
        issue = registry.created[issue_id]
        assert issue["is_fixable"] is False
        assert issue["severity"] == ir.IssueSeverity.WARNING
        assert _issue(entry, ISSUE_EMS_HEARTBEAT_LOST) not in registry.created
        registry.deleted.clear()
        registry.created.clear()
        for _ in range(5):
            runtime.coordinator.async_update_listeners()
        assert registry.created == {}
        assert registry.deleted == []  # No repair storage churn on unchanged polls.
        _set_healthy(entry, True)
        assert registry.deleted == [issue_id]
        assert monitor._active is None
        assert transport.writes == []  # Observing a failure must not resume charging.
        monitor.stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_short_failure_recovery_and_new_failure_get_separate_grace(monkeypatch):
    async def run():
        _, entry, _, registry, monitor = await _setup(monkeypatch)
        heartbeat = entry.runtime_data.heartbeat
        _set_healthy(entry, False)
        first = monitor._timer
        _set_healthy(entry, True)
        assert first.cancelled()
        assert monitor._timer is None
        assert registry.created == {}
        _set_healthy(entry, False)
        assert monitor._timer is not first
        _expire(monitor)
        assert _issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE) in registry.created
        monitor.stop()
        assert registry.created == {}
        assert not heartbeat._listeners
        # A queued callback or late observation after unload cannot recreate it.
        monitor._expired()
        monitor.check()
        assert registry.created == {}
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("suppression", ["read_only", "no_heartbeat", "unknown", "stopping", "unloading", "reauth"])
def test_disabled_unknown_and_stopping_heartbeats_do_not_raise_repairs(monkeypatch, suppression):
    async def run():
        hass, entry, _, registry, monitor = await _setup(
            monkeypatch, read_only=suppression == "read_only", healthy=None,
        )
        assert monitor._timer is None
        runtime = entry.runtime_data
        if suppression == "no_heartbeat":
            runtime.heartbeat = None
        elif suppression == "stopping":
            hass.is_stopping = True
        elif suppression == "unloading":
            entry.state = ConfigEntryState.UNLOAD_IN_PROGRESS
        elif suppression == "reauth":
            runtime.coordinator.last_exception = ConfigEntryAuthFailed("credential rejected")
        if suppression != "unknown":
            _set_healthy(entry, False)
        monitor.check()
        assert monitor._timer is None
        assert registry.created == {}
        monitor.stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_expiry_rechecks_stale_evidence_and_unload_cancels_pending_grace(monkeypatch):
    async def run():
        _, entry, _, registry, monitor = await _setup(monkeypatch, healthy=False)
        assert monitor._timer is not None  # Already unhealthy when subscribed.
        entry.state = ConfigEntryState.UNLOAD_IN_PROGRESS
        _expire(monitor)
        assert registry.created == {}
        entry.state = ConfigEntryState.SETUP_IN_PROGRESS
        monitor.check()
        timer = monitor._timer
        assert timer is not None
        monitor.stop()
        assert timer.cancelled()
        assert registry.created == {}
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_existing_firmware_200_repair_is_not_duplicated_until_connection_fails(monkeypatch):
    async def run():
        _, entry, transport, registry, monitor = await _setup(monkeypatch, healthy=False, error=200)
        assert set(registry.created) == {_issue(entry, ISSUE_EMS_HEARTBEAT_LOST)}
        assert monitor._timer is None
        entry.runtime_data.coordinator.async_set_update_error(UpdateFailed("bus disconnected"))
        _expire(monitor)
        assert _issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE) in registry.created
        # The previous confirmed 200 is retained; inability to read is not a cure.
        assert _issue(entry, ISSUE_EMS_HEARTBEAT_LOST) in registry.created
        assert transport.writes == []
        monitor.stop()
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("read_only", [True, False])
def test_http_outage_uses_last_app_policy_not_remote_control_unavailability(monkeypatch, read_only):
    async def run():
        _, entry, transport, registry, monitor = await _setup(monkeypatch, remote=True, read_only=read_only)
        runtime = entry.runtime_data
        runtime.client._mark_unavailable()
        assert runtime.heartbeat.healthy is None
        if read_only:
            assert monitor._timer is None
            assert registry.created == {}
        else:
            _expire(monitor)
            assert set(registry.created) == {_issue(entry, ISSUE_SERVICE_UNAVAILABLE)}
            assert registry.created[_issue(entry, ISSUE_SERVICE_UNAVAILABLE)]["is_fixable"] is False
        assert transport.writes == []
        monitor.stop()

    asyncio.run(run())


def test_reachable_app_dead_bus_has_heartbeat_repair_and_no_recovery_writes(monkeypatch):
    async def run():
        _, entry, transport, registry, monitor = await _setup(monkeypatch, remote=True)
        runtime = entry.runtime_data
        runtime.client.snapshot["heartbeat"]["healthy"] = False
        runtime.client.on_update()
        runtime.coordinator.async_set_update_error(UpdateFailed("wallbox unavailable at service"))
        assert runtime.client.connected
        _expire(monitor)
        assert set(registry.created) == {_issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE)}
        runtime.client.snapshot["heartbeat"]["healthy"] = True
        runtime.client.on_update()
        assert registry.created == {}
        assert transport.writes == []
        monitor.stop()

    asyncio.run(run())


def test_app_outage_that_becomes_bus_outage_keeps_grace_and_updates_explanation(monkeypatch):
    async def run():
        _, entry, _, registry, monitor = await _setup(monkeypatch, remote=True)
        remote = entry.runtime_data.client
        remote._mark_unavailable()
        timer = monitor._timer
        remote._connected = True
        remote.snapshot["heartbeat"]["healthy"] = False
        remote.on_update()
        assert monitor._timer is timer
        _expire(monitor)
        assert set(registry.created) == {_issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE)}
        remote._mark_unavailable()
        assert monitor._timer is None
        assert set(registry.created) == {_issue(entry, ISSUE_SERVICE_UNAVAILABLE)}
        remote._connected = True
        remote.snapshot["heartbeat"]["healthy"] = True
        remote.on_update()
        assert registry.created == {}
        monitor.stop()

    asyncio.run(run())


def test_monitor_can_start_without_heartbeat_and_cleans_stale_notice(monkeypatch):
    async def run():
        registry = _patch(monkeypatch)
        hass, entry, _ = await build_runtime(read_only=True)
        old_issue = _issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE)
        registry.created[old_issue] = {}
        repairs.async_setup_repairs(hass, entry)
        assert old_issue not in registry.created
        monitor = entry.unloads[-1].__self__
        assert monitor._timer is None
        monitor.stop()
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_definite_auth_failure_replaces_an_existing_connectivity_repair(monkeypatch):
    async def run():
        _, entry, _, registry, monitor = await _setup(monkeypatch, healthy=False)
        coordinator = entry.runtime_data.coordinator
        coordinator.async_set_update_error(UpdateFailed("network unavailable"))
        _expire(monitor)
        assert _issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE) in registry.created
        entry.runtime_data.client._transport.call = AsyncMock(
            side_effect=ServiceConnectionError("service_auth_failed", "credential rejected"),
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        assert registry.created == {}
        assert monitor._timer is None
        monitor.stop()
        await entry.runtime_data.client.async_close()
    asyncio.run(run())


def test_real_ha_reports_disconnected_bus_and_clears_when_heartbeat_returns(monkeypatch, tmp_path):
    """Real setup, entity listener, TimerHandle and issue registry; fake bus only."""
    async def run():
        transport = FakeModbusClient(device_bank())
        monkeypatch.setattr(repair_health, "HEARTBEAT_REPAIR_DELAY", 0.02)
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                entities = er.async_get(hass)
                entity_id = entities.async_get_entity_id("sensor", DOMAIN, f"{SERIAL_NUMBER}_connection_quality")
                assert entity_id is not None
                # A diagnostic sensor, off by default; the master reload adds it.
                entities.async_update_entity(entity_id, disabled_by=None)
                hass.config_entries.async_update_entry(entry, options={"control_mode": CONTROL_MODE_MASTER})
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service
                registry = ir.async_get(hass)
                issue_id = _issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE)
                assert await service.heartbeat.async_beat_once()
                service.heartbeat._set_healthy(True)
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(entity_id).attributes["heartbeat"] == "ok"
                transport.read_exceptions[R.EVSE_STATE.address] = raise_connection_error()
                transport.write_exceptions[R.HEARTBEAT.address] = raise_connection_error()
                with pytest.raises(AmtronBusError):
                    await service.refresh()
                # This same deadline fires when no acknowledgement arrives. The
                # sensor is unavailable while nothing can be read; the heartbeat
                # still tracks its health and raises the repair.
                service.heartbeat._deadline_expired()
                await runtime.coordinator.async_refresh()
                assert not runtime.coordinator.last_update_success
                await asyncio.sleep(0.04)
                await hass.async_block_till_done()
                assert hass.states.get(entity_id).state == "unavailable"
                assert runtime.heartbeat.healthy is False
                assert registry.async_get_issue(DOMAIN, issue_id) is not None
                assert registry.async_get_issue(DOMAIN, _issue(entry, ISSUE_EMS_HEARTBEAT_LOST)) is None
                assert all(address == R.HEARTBEAT.address for address, *_ in transport.writes)

                transport.read_exceptions.clear()
                transport.write_exceptions.clear()
                await service.refresh()
                assert await service.heartbeat.async_beat_once()
                service.heartbeat._set_healthy(True)
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(entity_id).attributes["heartbeat"] == "ok"
                assert registry.async_get_issue(DOMAIN, issue_id) is None

                # A config reload stops the old heartbeat; that shutdown must
                # neither create nor leave a repair in the new read-only entry.
                hass.config_entries.async_update_entry(entry, options={})
                await hass.async_block_till_done()
                await asyncio.sleep(0.04)
                assert not heartbeat_running(entry.runtime_data.heartbeat)
                assert not hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service.heartbeat.running
                assert registry.async_get_issue(DOMAIN, issue_id) is None
            finally:
                await hass.async_stop()

    asyncio.run(run())


def test_connection_repair_explanations_exist_in_every_language():
    component = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    for path in [component / "strings.json", *sorted((component / "translations").glob("*.json"))]:
        issues = json.loads(path.read_text())["issues"]
        for key in (ISSUE_HEARTBEAT_UNAVAILABLE, ISSUE_SERVICE_UNAVAILABLE):
            assert issues[key]["title"], (path.name, key)
            assert issues[key]["description"], (path.name, key)


def test_a_direct_entry_without_its_service_misses_the_heartbeat_not_an_app(monkeypatch):
    async def run():
        _, entry, transport, registry, monitor = await _setup(monkeypatch)
        # There is no app that could keep a heartbeat going on its own.
        entry.runtime_data.client._mark_unavailable()
        _expire(monitor)
        assert set(registry.created) == {_issue(entry, ISSUE_HEARTBEAT_UNAVAILABLE)}
        assert transport.writes == []
        monitor.stop()
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_a_failing_heartbeat_listener_fails_neither_the_poll_nor_the_others(monkeypatch, caplog):
    async def run():
        _, entry, _transport, _registry, monitor = await _setup(monkeypatch)
        client = entry.runtime_data.client
        heartbeat = ServiceHeartbeat(client)
        seen = []

        def broken():
            raise RuntimeError("entity write failed")

        heartbeat.async_add_listener(broken)
        heartbeat.async_add_listener(lambda: seen.append(heartbeat.healthy))
        client.snapshot["heartbeat"]["healthy"] = False
        heartbeat.update()
        assert seen == [False]
        assert "Heartbeat: listener failed" in caplog.text
        monitor.stop()
        await client.async_close()

    asyncio.run(run())
