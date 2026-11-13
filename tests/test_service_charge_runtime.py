"""Real client/control/service integration of the bounded local job owner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.control import (
    ControlOptions,
    _PendingCommand,
    _TimedCommandCancelledError,
)
from custom_components.mennekes_amtron_rtu.device.data import (
    DeviceIdentity,
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_console_runtime import (
    ConsoleModbusService,
)
from custom_components.mennekes_amtron_rtu.device.service_job_control import (
    JobAmtronControl,
)
from custom_components.mennekes_amtron_rtu.device.service_job_runtime import (
    JobModbusService,
)
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.fakes import FakeModbusClient, device_bank, put

TOKEN = "service-test-credential-32-characters"  # noqa: S105 - fixture


def current_writes(transport):
    return [decode_register(R.CHARGING_CURRENT_EMS, words) for address, words in transport.writes if address == R.CHARGING_CURRENT_EMS.address]


def request(*, future=False):
    now = datetime.now(UTC)
    return {
        "id": "a" * 32,
        "start_at": (now + timedelta(hours=1) if future else now - timedelta(seconds=10)).isoformat(),
        "finish_at": (now + timedelta(hours=3)).isoformat(),
        "current_a": 12, "energy_kwh": 10,
    }


@asynccontextmanager
async def job_service(tmp_path, *, read_only=False):
    transport = FakeModbusClient(device_bank(evse_state=2, charging_current=8))
    clock = [1000.0]
    client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport, monotonic=lambda: clock[0])
    service = JobModbusService(ServerConfig(client.config, TOKEN, read_only=read_only, current_cap=16), tmp_path / "intent.json", client=client)
    try:
        await service._initialize()
        await service.heartbeat.async_stop()
        transport.writes.clear()
        service.control._monotonic = lambda: clock[0]
        yield service, transport, clock
    finally:
        await service.stop()


@pytest.mark.parametrize("future", [False, True])
def test_local_order_uses_only_the_existing_guarded_current_path(tmp_path, future):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            c = s.jobs
            values = c.snapshot()
            before = list(transport.reads)
            preview = c.preview(request(future=future), revision=values["revision"], scope=values["scope"])
            assert transport.reads == before
            result = await c.create(preview["request"], revision=preview["revision"], scope=preview["scope"])
            assert result["job"]["state"] == ("scheduled" if future else "charging")
            assert current_writes(transport)[-1] == (1 if future else 12)
            assert all(address == R.CHARGING_CURRENT_EMS.address for address, _words in transport.writes)
            clock[0] += 1000
            if future:
                actual = c.inputs
                c.inputs = lambda: replace(actual(), now=datetime.now(UTC) + timedelta(hours=2))
                await c.tick()
                assert current_writes(transport)[-1] == 12
            clock[0] += 10
            put(transport.bank, R.ENERGY_TOTAL, 1244.5)
            s._poller._slow_read_at = None
            await s.refresh()
            assert c.stop_requested()
            await c.tick()
            assert c.job.state == "reached"
            assert current_writes(transport)[-1] == 1
    asyncio.run(run())


@pytest.mark.parametrize(("command", "value"), [
    ("set_current", 16), ("resume", None), ("timed_current", 16),
    ("solar", 2), ("phases", 1), ("recover", None), ("restart", None),
    ("release", True), ("lock", False),
])
def test_competing_clients_cannot_take_over_an_accepted_job(tmp_path, command, value):
    async def run():
        async with job_service(tmp_path) as (s, transport, _clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            before = list(transport.writes)
            with pytest.raises(DeviceError, match="energy_goal_stop_active"):
                await s.command({"command": command, "value": value, "expected_serial_number": s.identity.serial_number})
            assert transport.writes == before
            assert s.jobs.job.state == "charging"
    asyncio.run(run())


@pytest.mark.parametrize(("command", "value"), [
    ("pause", None), ("release", False), ("lock", True), ("cancel_pending", None),
])
def test_manual_restrictive_commands_latch_the_job_stop(tmp_path, command, value):
    async def run():
        async with job_service(tmp_path) as (s, _transport, clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            clock[0] += 1000
            await s.command({"command": command, "value": value, "owner": "unrelated", "expected_serial_number": s.identity.serial_number})
            assert s.jobs.job.state == "stopped"
            assert s.jobs.stop_requested()
    asyncio.run(run())


def test_lost_connection_and_changed_vehicle_source_never_rearm(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, _transport, clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            clock[0] += 10
            s.control.soc_goal.configure_source("other")
            assert s.jobs.stop_requested()
            await s.jobs.tick()
            assert s.jobs.job.reason == "vehicle_changed"
            s._success = False
            await s.jobs.tick()
            assert not s.jobs.snapshot()["available"]
            await s.refresh()
            assert s.jobs.job.terminal
            with pytest.raises(DeviceError, match="device_identity_changed"):
                await s.command({"command": "pause", "expected_serial_number": "other"})
    asyncio.run(run())


@pytest.mark.parametrize(("field", "value"), [
    ("value", None), ("value", True), ("value", float("nan")),
    ("observed_at", None), ("observed_at", True),
    ("observed_at", 0), ("observed_at", float("inf")),
])
def test_only_genuine_recent_meter_samples_reach_the_job(tmp_path, field, value):
    async def run():
        async with job_service(tmp_path) as (s, _transport, _clock):
            s._energy_sample[field] = value
            assert not s._job_inputs().meter_fresh
            assert s._job_inputs().total_kwh is None
    asyncio.run(run())


def test_service_snapshot_readback_and_stale_samples_are_passive(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, _clock):
            before = (list(transport.reads), list(transport.writes))
            assert s._job_inputs().permitted
            s.data.current_write_generation -= 1
            assert s._job_inputs().current_a is None
            s._energy_sample = None
            assert not s._job_inputs().meter_fresh
            original = s.control
            s.control = None
            assert not s._job_inputs().permitted
            assert s._job_inputs().scope is None
            assert s._job_inputs().current_cap == 6
            s.control = original
            s.control.goal_controller = None
            s.control.soc_goal_controller = None
            assert not s._job_inputs().independent_stop
            assert (transport.reads, transport.writes) == before
    asyncio.run(run())


def test_read_only_service_and_unconfigured_actuator_fail_closed(tmp_path):
    async def run():
        async with job_service(tmp_path, read_only=True) as (s, _transport, _clock):
            with pytest.raises(JobRequestError, match="job_not_permitted"):
                await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            original = s.control
            s.control = None
            try:
                with pytest.raises(DeviceError, match="service_unavailable"):
                    await s._apply_job_current(12, "job", datetime.now(UTC))
            finally:
                s.control = original
    asyncio.run(run())


def test_unplug_ends_the_scope_and_restart_cannot_resume_saved_order(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            clock[0] += 10
            await s.jobs.stop()
            put(transport.bank, R.EVSE_STATE, 1)
            put(transport.bank, R.CP_STATE, 10)
            await s.refresh()
            assert s.jobs.job.scope_ended
            assert s._job_scope is None
            await s.jobs.tick()
        async with job_service(tmp_path) as (s, transport, _clock):
            assert s.jobs.job.terminal
            assert not s.jobs.holding
            assert current_writes(transport) == []
    asyncio.run(run())


def test_corrupt_storage_does_not_restart_loop_or_advertise_permission(tmp_path, caplog):
    async def run():
        (tmp_path / "charge-job.json").write_text("not-json")
        async with job_service(tmp_path) as (s, transport, _clock):
            assert s.jobs.storage_error
            await s.jobs.tick()
            assert current_writes(transport)[-1] == 1
            assert "starts fenced" in caplog.text
    asyncio.run(run())


def test_initialization_hook_and_scheduler_lifetime(monkeypatch, tmp_path):
    async def run():
        service = JobModbusService(ServerConfig(SerialConfig("/dev/fake"), TOKEN), tmp_path / "intent.json")
        monkeypatch.setattr(ConsoleModbusService, "_initialize", AsyncMock())
        with pytest.raises(RuntimeError, match="not configured"):
            await service._initialize()
        base_run = AsyncMock()
        monkeypatch.setattr(ConsoleModbusService, "_run", base_run)
        with pytest.raises(ExceptionGroup, match="TaskGroup"):
            await service._run()
        base_run.assert_awaited_once()
        for jobs in (None, Mock(storage_error=False, action_error=None), Mock(storage_error=True, action_error=None)):
            if jobs is not None:
                jobs.tick = AsyncMock()
            service.jobs = jobs
            sleeping = AsyncMock(side_effect=asyncio.CancelledError())
            with monkeypatch.context() as patch:
                patch.setattr(asyncio, "sleep", sleeping)
                with pytest.raises(asyncio.CancelledError):
                    await service._jobs_loop()
            sleeping.assert_awaited_once_with(5 if jobs is not None and jobs.storage_error else 1)
        service.jobs = None
        await service.stop()
    asyncio.run(run())


def control():
    client = Mock()
    client.write_state = WriteState()
    return JobAmtronControl(client, diagnostics=WriteDiagnostics(), identity=DeviceIdentity(layout_version=259), options=ControlOptions(False, 16), state=WallboxData)


def test_policy_hooks_preserve_legacy_owners_and_gate_all_enabling_writes():
    c = control()
    command = _PendingCommand("set_current", 12, owner="job", deadline=datetime.now(UTC) + timedelta(hours=1))
    assert c._timed_cancellation_reason(command) == "energy_goal"
    c.configure_service_policy(lambda _owner, _enabling: None, lambda: False)
    assert c._timed_cancellation_reason(command) == "energy_goal"
    assert c._timed_cancellation_reason(_PendingCommand("pause", 1)) is None
    c.configure_service_policy(lambda _owner, _enabling: True, lambda: False)
    assert c._timed_cancellation_reason(command) is None
    assert c._timed_cancellation_reason(replace(command, deadline=datetime.now(UTC) - timedelta(seconds=1))) == "deadline_expired"
    c.configure_service_policy(lambda _owner, _enabling: False, lambda: True)
    assert c._timed_cancellation_reason(command) == "energy_goal"
    with pytest.raises(DeviceError, match="energy_goal_stop_active"):
        c._check_write_allowed(R.CHARGING_CURRENT_EMS, 12, recovery_release=False)
    c._active_command = command
    with pytest.raises(DeviceError, match="energy_goal_stop_active"):
        c._check_write_allowed(R.LOCK_EVSE, 0, recovery_release=False)
    c._active_command = None
    c.configure_service_policy(lambda _owner, _enabling: None, lambda: False)
    c._check_write_allowed(R.CHARGING_CURRENT_EMS, 1, recovery_release=False)
    c._check_write_allowed(R.CHARGING_CURRENT_EMS, 12, recovery_release=False)


@pytest.mark.parametrize(("bad_register", "bad_value"), [
    (None, None), (R.EVSE_STATE, 1), (R.ERROR_CODE, 200), (R.LOCK_EVSE, 1), (R.CHARGING_RELEASE, 0),
])
def test_live_bus_preconditions_are_stronger_than_the_cached_job_intent(bad_register, bad_value):
    async def run():
        c = control()
        c.configure_service_policy(lambda _owner, _enabling: True, lambda: False)
        c._active_command = _PendingCommand("set_current", 12, owner="job")
        # RegisterSpec contains enum dictionaries and is not a hashable key.
        # Use the same stable names as decoded WallboxData instead.
        values = {R.EVSE_STATE.key: 2, R.CP_STATE.key: 11, R.ERROR_CODE.key: 0, R.LOCK_EVSE.key: 0, R.CHARGING_RELEASE.key: 1}
        if bad_register is not None:
            values[bad_register.key] = bad_value
        async def read(register):
            return values[register.key]
        if bad_register is not None:
            with pytest.raises(_TimedCommandCancelledError, match="unknown"):
                await c._async_check_timed_vehicle(read)
        else:
            await c._async_check_timed_vehicle(read)
            c._identity.layout_version = 256
            await c._async_check_timed_vehicle(read)
            c.configure_service_policy(lambda _owner, _enabling: None, lambda: False)
            await c._async_check_timed_vehicle(read)
            c._active_command = None
            await c._async_check_timed_vehicle(read)
    asyncio.run(run())


def test_ha_planner_detach_does_not_cancel_a_service_order(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            before = list(transport.writes)
            await s.command({"command": "cancel_timed_current", "owner": "ha-departure", "expected_serial_number": s.identity.serial_number})
            assert s.jobs.job.state == "charging"
            assert transport.writes == before
            with pytest.raises(ValueError, match="stop command field"):
                await s.command({"command": "pause", "address": 123, "expected_serial_number": s.identity.serial_number})
            assert s.jobs.job.state == "charging"
            clock[0] += 1000
            await s.command({"command": "cancel_timed_current", "owner": f"service-job:{s.jobs.job.identifier}", "expected_serial_number": s.identity.serial_number})
            assert s.jobs.job.state == "stopped"
    asyncio.run(run())
