"""A departure plan owns only its own bounded direct/service command queue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.data import WallboxData, WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device import control as device_control
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, current_writes, service_fixture

OWNER = "timed:entry:one-trip"


def future() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def test_expired_trip_never_writes_or_remembers_its_current() -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        control.charging_setpoint = 12
        await control.async_set_timed_current(6, deadline=datetime.now(UTC) - timedelta(seconds=1), owner=OWNER)
        assert harness.written(R.CHARGING_CURRENT_EMS) == []
        assert control.charging_setpoint == 12
        assert control.pending_action is None
        event = control.events.snapshot()["events"][-1]
        assert event["outcome"] == "cancelled"
        assert event["reason"] == "deadline_expired"

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, 6.0])
def test_timed_current_uses_normal_limits_and_pause_rules(value: float | None) -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        await control.async_set_timed_current(value, deadline=future(), owner=OWNER)
        assert control.pause_requested is (value is None)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1

    asyncio.run(run())


@pytest.mark.parametrize("value", [True, 0, 1, float("nan"), float("inf"), "6"])
def test_invalid_timed_current_cannot_bypass_current_validation(value) -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        with pytest.raises(ServiceValidationError):
            await control.async_set_timed_current(value, deadline=future(), owner=OWNER)
        assert harness.written(R.CHARGING_CURRENT_EMS) == []

    asyncio.run(run())


@pytest.mark.parametrize(("owner", "deadline"), [("", future()), ("x" * 129, future()), (None, future()), (OWNER, future().replace(tzinfo=None)), (OWNER, None)])
def test_timed_owner_and_deadline_are_validated(owner, deadline) -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        with pytest.raises(ValueError, match="timed command"):
            await control.async_set_timed_current(6, deadline=deadline, owner=owner)
        assert harness.written(R.CHARGING_CURRENT_EMS) == []

    asyncio.run(run())


def test_expiry_cancels_deferred_resume_without_a_running_ha(monkeypatch) -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        deadline = future()
        try:
            await control.async_pause_charging()
            await control.async_set_timed_current(6, deadline=deadline, owner=OWNER)
            assert control.pending_action == "set_current"
            assert control.charging_setpoint == 16

            class ExpiredDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return deadline + timedelta(seconds=1)

            monkeypatch.setattr(device_control, "datetime", ExpiredDatetime)
            release.set()
            await asyncio.wait_for(control._flush_task, 2)
            assert control.pending_action is None
            assert control.pause_requested is True
            assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
            assert control.events.snapshot()["events"][-1]["reason"] == "deadline_expired"
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


def test_timed_cancel_and_submit_preserve_manual_commands() -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        try:
            await control.async_pause_charging()
            await control.async_set_timed_current(6, deadline=future(), owner=OWNER)
            await control.async_cancel_timed_current("another-owner")
            assert control.pending_action == "set_current"
            control._options = ControlOptions(read_only=True)
            await control.async_cancel_timed_current(OWNER)
            assert control.pending_action is None
            control._options = ControlOptions(read_only=False)
            await control.async_set_charging_current(10)
            assert control.pending_action == "set_current"
            await control.async_set_timed_current(None, deadline=future(), owner=OWNER)
            await control.async_cancel_timed_current(OWNER)
            assert control._pending.value == 10
            assert control._pending.owner is None
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


def test_expiry_after_waiting_for_physical_identity_never_sends(monkeypatch) -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        deadline = future()
        started, release = asyncio.Event(), asyncio.Event()
        original = harness.client._async_transaction_client

        async def delayed_identity(**kwargs):
            client = await original(**kwargs)
            started.set()
            await release.wait()
            return client

        harness.client._async_transaction_client = delayed_identity
        writing = asyncio.create_task(control.async_set_timed_current(6, deadline=deadline, owner=OWNER))
        try:
            await asyncio.wait_for(started.wait(), 2)

            class ExpiredDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return deadline + timedelta(seconds=1)

            monkeypatch.setattr(device_control, "datetime", ExpiredDatetime)
            release.set()
            await asyncio.wait_for(writing, 2)
            assert harness.written(R.CHARGING_CURRENT_EMS) == []
            assert harness.diagnostics.writes_sent == 0
            assert harness.client.write_state.current_write_generation == 0
            assert control.events.snapshot()["events"][-1]["reason"] == "deadline_expired"
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("finish", ["cancel", "expire"])
def test_remote_timed_queue_and_readonly_cancellation_roundtrip(tmp_path, monkeypatch, finish) -> None:
    async def run() -> None:
        service, transport, _clock = await service_fixture(tmp_path)
        release = asyncio.Event()
        try:
            await service.heartbeat.async_stop()
            service.control._sleep = lambda _delay: release.wait()
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16), state=lambda: WallboxData(), request_refresh=None)
                await control.async_set_timed_current(None, deadline=future(), owner=OWNER)
                deadline = future()
                await control.async_set_timed_current(6, deadline=deadline, owner=OWNER)
                assert service.control.pending_action == "set_current"
                assert current_writes(transport) == [1]
                if finish == "cancel":
                    service.control._options = ControlOptions(read_only=True)
                    service.connection.available = False
                    remote.apply_snapshot(service.snapshot(), require_available=False)
                    await control.async_cancel_timed_current(OWNER)
                else:
                    # HA has gone away: only the independent service can expire
                    # a pending resume when its trip's deadline passes.
                    await remote.async_close()

                    class ExpiredDatetime(datetime):
                        @classmethod
                        def now(cls, tz=None):
                            return deadline + timedelta(seconds=1)

                    monkeypatch.setattr(device_control, "datetime", ExpiredDatetime)
                    release.set()
                    await asyncio.wait_for(service.control._flush_task, 2)
                assert service.control.pending_action is None
                assert current_writes(transport) == [1]
                await remote.async_close()
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("extra", [
    {"deadline": "invalid"}, {"deadline": "2026-01-01T00:00:00"},
    {"deadline": True}, {"deadline": "x" * 65}, {"owner": ""},
    {"owner": None}, {"owner": "x" * 129}, {"value": True}, {"value": 1},
])
def test_service_rejects_malformed_timed_commands(tmp_path, extra) -> None:
    async def run() -> None:
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            with pytest.raises(ValueError, match=r"timed command|isoformat|number|numeric|range"):
                await service.command({
                    "command": "timed_current", "value": 6, "deadline": future().isoformat(),
                    "owner": OWNER, "expected_serial_number": service.identity.serial_number, **extra,
                })
            assert current_writes(transport) == []
        finally:
            await service.stop()

    asyncio.run(run())


def test_remote_timed_current_keeps_local_cap_and_rejects_invalid_values(tmp_path) -> None:
    async def run() -> None:
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 8), state=lambda: WallboxData(), request_refresh=None)
                for value in (True, float("nan"), 0, 1, "6"):
                    with pytest.raises(ServiceValidationError):
                        await control.async_set_timed_current(value, deadline=future(), owner=OWNER)
                assert current_writes(transport) == []
                await control.async_set_timed_current(32, deadline=future(), owner=OWNER)
                assert current_writes(transport) == [8]
                await remote.async_close()
        finally:
            await service.stop()

    asyncio.run(run())
