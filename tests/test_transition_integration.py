"""Queued transitions remain visible and bounded across the HA/service boundary."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import (
    RemoteClient,
    RemoteControl,
    _pending_status,
)
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.sensor import async_setup_entry
from tests.ha_fakes import build_runtime, collect
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


def test_pending_sensors_publish_fixed_deadlines_without_poll_churn():
    async def run():
        hass, entry, transport = await build_runtime()
        runtime = entry.runtime_data
        control = runtime.control
        coordinator = runtime.coordinator
        coordinator.control_status = lambda: (control.pending_action, control.pending_until)
        control._request_refresh = coordinator.async_refresh
        entities = {}
        await async_setup_entry(hass, entry, lambda items: entities.update(collect(items)))
        action = entities["pending_change"]

        def deadline():
            return (action.extra_state_attributes or {}).get("due")

        assert action.available
        assert action.native_value == "none"
        assert deadline() is None
        notifications = []
        remove = coordinator.async_add_listener(lambda: notifications.append(coordinator.data))
        try:
            await control.async_pause_charging()
            await control.async_resume_charging()
            expected = deadline()
            assert isinstance(expected, datetime)
            assert expected.tzinfo == UTC
            assert action.native_value == "resume"
            before = len(notifications)
            for _ in range(4):
                await coordinator.async_refresh()
                assert deadline() == expected
            assert len(notifications) == before
            # While still paused, a block switches no charge: it applies at
            # once and replaces the queued start.
            await control.async_set_charging_release(False)
            assert action.native_value == "none"
            assert deadline() is None
            assert transport.bank[R.CHARGING_RELEASE.address] == 0
            coordinator.last_update_success = False
            assert not action.available
        finally:
            remove()
            await control.async_shutdown()
            await runtime.client.async_close()

    asyncio.run(run())


def test_service_queue_survives_client_restart_and_persists_executed_intent(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        wake = asyncio.Event()
        sleeping = asyncio.Event()

        async def wait(delay):
            sleeping.set()
            await wake.wait()
            clock[0] += delay

        service.control._sleep = wait
        try:
            await service.command({"command": "pause", "expected_serial_number": "ABC123456789"})
            clock[0] += 6
            await service.command({"command": "resume", "expected_serial_number": "ABC123456789"})
            await asyncio.wait_for(sleeping.wait(), 1)
            state = service.snapshot()
            assert state["control"]["state_change_interval_seconds"] == 300
            assert state["control"]["pending_action"] == "resume"
            deadline = state["control"]["pending_until"]
            assert current_writes(transport) == [1]
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                first = await remote.update()
                assert first.pending_change == "resume"
                assert first.pending_change_until.isoformat() == deadline
                remote_control = RemoteControl(
                    remote, diagnostics=WriteDiagnostics(), options=ControlOptions(),
                    state=lambda: first, request_refresh=remote.update,
                )
                assert remote_control.pending_action == "resume"
                assert remote_control.pending_until == first.pending_change_until
                await remote_control.async_shutdown()
                await remote.async_close()
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                assert remote.snapshot["control"]["pending_until"] == deadline
                assert service.control.pending_action == "resume"
                wake.set()
                await asyncio.wait_for(service.control._flush_task, 2)
                data = await remote.update()
                assert data.pending_change is None
                assert data.pending_change_until is None
                assert current_writes(transport) == [1, 8]
                saved = json.loads((tmp_path / "intent.json").read_text())
                assert saved["pause_requested"] is False
                assert saved["charging_setpoint"] == 8
                assert service.heartbeat.running
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["stopped", "stale"])
def test_service_rechecks_availability_before_executing_queue(tmp_path, failure):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        wake = asyncio.Event()

        async def wait(delay):
            await wake.wait()
            clock[0] += delay

        service.control._sleep = wait
        try:
            await service.control.async_pause_charging()
            clock[0] += 6
            await service.control.async_resume_charging()
            if failure == "stopped":
                service._stopping = True
            else:
                service._success = False
            wake.set()
            await asyncio.wait_for(service.control._flush_task, 2)
            assert current_writes(transport) == [1]
            assert service.control.pending_action is None
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("metadata", [
    {"pending_action": "resume"},
    {"pending_until": None},
    {"pending_action": [], "pending_until": None},
    {"pending_action": "unknown", "pending_until": "2026-11-06T13:00:00+00:00"},
    {"pending_action": "resume", "pending_until": None},
    {"pending_action": "resume", "pending_until": "x" * 41},
    {"pending_action": "resume", "pending_until": "not a date"},
    {"pending_action": "resume", "pending_until": "2026-11-06T13:00:00"},
    {"pending_action": "resume", "pending_until": "0001-01-01T00:00:00+01:00"},
    {"pending_action": None, "pending_until": "2026-11-06T13:00:00+00:00"},
    {"state_change_interval_seconds": True},
    {"state_change_interval_seconds": -1},
    {"state_change_interval_seconds": 3601},
    {"state_change_interval_seconds": float("nan")},
])
def test_invalid_queue_metadata_invalidates_remote_connection(tmp_path, metadata):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://localhost:8765", TOKEN)
                remote.apply_snapshot(service.snapshot())
                payload = copy.deepcopy(service.snapshot())
                for key in ("pending_action", "pending_until", "state_change_interval_seconds"):
                    payload["control"].pop(key, None)
                payload["control"].update(metadata)
                with pytest.raises(ServiceConnectionError, match="invalid service snapshot"):
                    remote.apply_snapshot(payload)
                assert not remote.connected
        finally:
            await service.stop()

    asyncio.run(run())


def test_missing_queue_metadata_is_rejected_and_timezone_offsets_are_supported(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://localhost:8765", TOKEN)
                payload = copy.deepcopy(service.snapshot())
                for key in ("pending_action", "pending_until", "state_change_interval_seconds"):
                    payload["control"].pop(key)
                with pytest.raises(ServiceConnectionError, match=r"control\.pending_change"):
                    remote.apply_snapshot(payload)
                assert not remote.connected
            action, deadline = _pending_status({
                "pending_action": "resume", "pending_until": "2026-11-06T14:00:00+01:00",
            })
            assert action == "resume"
            assert deadline == datetime(2026, 11, 6, 13, tzinfo=UTC)
        finally:
            await service.stop()

    asyncio.run(run())
