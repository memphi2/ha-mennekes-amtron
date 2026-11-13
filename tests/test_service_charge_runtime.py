"""Real client/control/service integration of the bounded local job owner."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import (
    service_job_runtime as job_runtime,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.control import (
    AmtronControl,
    ControlOptions,
    ServiceChargePolicy,
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
async def job_service(tmp_path, *, read_only=False, bank=None):
    transport = FakeModbusClient(bank or device_bank(evse_state=2, charging_current=8))
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
            with pytest.raises(DeviceError, match="service_job_active"):
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


def test_meter_freshness_follows_the_monotonic_clock_not_a_clock_step(tmp_path, monkeypatch):
    async def run():
        async with job_service(tmp_path) as (s, _transport, _clock):
            assert s._job_inputs().meter_fresh
            # The wall clock steps back a little, or forward a few minutes:
            # the reading is as recent as before.
            for step in (-4, 300):
                s._energy_sample["observed_at"] = time.time() + step
                assert s._job_inputs().meter_fresh
            # The reading itself aged on the monotonic clock.
            monkeypatch.setattr(job_runtime.time, "monotonic", lambda: s._energy_sample_at + 91)
            assert not s._job_inputs().meter_fresh
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
    return AmtronControl(client, diagnostics=WriteDiagnostics(), identity=DeviceIdentity(layout_version=259), options=ControlOptions(False, 16), state=WallboxData)


def test_policy_hooks_preserve_legacy_owners_and_gate_all_enabling_writes():
    c = control()
    command = _PendingCommand("set_current", 12, owner="job", deadline=datetime.now(UTC) + timedelta(hours=1))
    assert c._timed_cancellation_reason(command) == "energy_goal"
    c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: None, lambda: False))
    assert c._timed_cancellation_reason(command) == "energy_goal"
    assert c._timed_cancellation_reason(_PendingCommand("pause", 1)) is None
    c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: True, lambda: False))
    assert c._timed_cancellation_reason(command) is None
    assert c._timed_cancellation_reason(replace(command, deadline=datetime.now(UTC) - timedelta(seconds=1))) == "deadline_expired"
    c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: False, lambda: True))
    assert c._timed_cancellation_reason(command) == "service_job"
    # A stop that only the app's order requests names the order, not a target.
    with pytest.raises(DeviceError, match="service_job_active"):
        c._check_write_allowed(R.CHARGING_CURRENT_EMS, 12, recovery_release=False)
    c._active_command = command
    with pytest.raises(DeviceError, match="service_job_active"):
        c._check_write_allowed(R.LOCK_EVSE, 0, recovery_release=False)
    c._active_command = None
    c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: None, lambda: False))
    c._check_write_allowed(R.CHARGING_CURRENT_EMS, 1, recovery_release=False)
    c._check_write_allowed(R.CHARGING_CURRENT_EMS, 12, recovery_release=False)


def test_stop_error_names_a_charge_target_before_the_service_order():
    c = control()
    c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: None, lambda: True))
    with pytest.raises(DeviceError, match="service_job_active"):
        raise c.stop_error()
    c.goal_controller = Mock(stop_requested=True)
    with pytest.raises(DeviceError, match="energy_goal_stop_active"):
        raise c.stop_error()
    c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: False, lambda: True))
    command = _PendingCommand("set_current", 12, owner="job", deadline=datetime.now(UTC) + timedelta(hours=1))
    assert c._timed_cancellation_reason(command) == "energy_goal"
    c.set_service_policy(None)
    c.goal_controller = None
    with pytest.raises(DeviceError, match="energy_goal_stop_active"):
        raise c.stop_error()


@pytest.mark.parametrize(("bad_register", "bad_value"), [
    (None, None), (R.EVSE_STATE, 1), (R.ERROR_CODE, 200), (R.LOCK_EVSE, 1), (R.CHARGING_RELEASE, 0),
])
def test_live_bus_preconditions_are_stronger_than_the_cached_job_intent(bad_register, bad_value):
    async def run():
        c = control()
        c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: True, lambda: False))
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
            c.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: None, lambda: False))
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


def test_one_unreadable_plug_state_keeps_the_order_and_its_scope(tmp_path, monkeypatch):
    import custom_components.mennekes_amtron_rtu.device.service_job_runtime as runtime

    async def run():
        async with job_service(tmp_path) as (s, _transport, clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            scope = s._job_scope
            real = runtime.snapshot_vehicle_connected
            monkeypatch.setattr(runtime, "snapshot_vehicle_connected", lambda *_args: None)
            clock[0] += 10
            await s.refresh()
            await s.jobs.tick()
            assert s._job_scope == scope
            assert not s.jobs.job.terminal
            # Starts stay fenced while the plug state is unknown.
            assert s.jobs.stop_requested()
            monkeypatch.setattr(runtime, "snapshot_vehicle_connected", real)
            clock[0] += 10
            await s.refresh()
            await s.jobs.tick()
            assert s._job_scope == scope
            assert s.jobs.job.state == "charging"
            # A lasting gap could hide an unplug: the scope goes.
            monkeypatch.setattr(runtime, "snapshot_vehicle_connected", lambda *_args: None)
            await s.refresh()
            s._job_unknown_since -= runtime.UNKNOWN_GRACE.total_seconds()
            await s.refresh()
            assert s._job_scope is None
    asyncio.run(run())


def test_an_unforeseen_job_failure_does_not_stop_the_service(tmp_path, caplog):
    async def run():
        service = JobModbusService(ServerConfig(SerialConfig("/dev/fake"), TOKEN), tmp_path / "intent.json")
        service.jobs = Mock(storage_error=False, action_error=None)
        service.jobs.tick = AsyncMock(side_effect=RuntimeError("boom"))
        sleeping = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(asyncio, "sleep", sleeping), pytest.raises(asyncio.CancelledError):
            await service._jobs_loop()
        assert "Charge job tick failed" in caplog.text
        assert service.jobs.action_error == "command_unconfirmed"
        sleeping.assert_awaited_once_with(5)
        service.jobs = None
        await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("replugged", [False, True])
def test_an_app_restart_continues_the_order_only_for_the_same_plug_in(tmp_path, replugged):
    async def run():
        bank = device_bank(evse_state=2, charging_current=8)
        async with job_service(tmp_path, bank=bank) as (s, _transport, _clock):
            await s.jobs.create(request(), revision=s.jobs.revision, scope=s.jobs.snapshot()["scope"])
            assert s.jobs.job.state == "charging"
            assert (s.jobs.job.session.duration_s, s.jobs.job.session.count) == (600, 42)
            before = s.jobs.job.binding
        if replugged:
            # Unplugged and plugged in again while the app was away.
            put(bank, R.SESSION_DURATION, 20)
            put(bank, R.SESSIONS_TOTAL, 43)
        async with job_service(tmp_path, bank=bank) as (s, transport, clock):
            assert (s.jobs.job.state, s.jobs.job.reason) == ("suspended", "service_restarted")
            clock[0] += 1000
            await s.jobs.tick()
            if replugged:
                assert (s.jobs.job.state, s.jobs.job.reason) == ("interrupted", "service_restarted")
                assert current_writes(transport)[-1] == 1
                # Once the pause reads back, Home Assistant controls charging.
                clock[0] += 10
                await s.refresh()
                await s.jobs.tick()
                assert not s.jobs.holding
            else:
                assert s.jobs.job.state == "charging"
                assert s.jobs.job.binding not in (before, None)
                assert current_writes(transport)[-1] == 12
    asyncio.run(run())


def test_the_session_record_follows_only_fresh_reads_of_this_connection(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            first = s.jobs.inputs().session
            assert (first.duration_s, first.energy_kwh, first.count) == (600, 1.25, 42)
            put(transport.bank, R.SESSION_DURATION, 660)
            s._poller._active_read_at = None
            clock[0] += 10
            await s.refresh()
            assert s.jobs.inputs().session.duration_s == 660
            assert s.jobs.inputs().session.at > first.at
            # A failed poll carries nothing forward under a new read time.
            with patch.object(s._poller, "async_poll", AsyncMock(side_effect=DeviceError("service_unavailable"))), pytest.raises(DeviceError):
                await s.refresh()
            assert s._job_session["duration_s"] == 660
            transport.bank[R.EVSE_STATE.address] = 1
            transport.bank[R.CP_STATE.address] = 10
            s._poller._active_read_at = s._poller._slow_read_at = None
            clock[0] += 10
            await s.refresh()
            assert s.jobs.inputs().session is None
            assert s._job_session is None
    asyncio.run(run())


def test_an_order_waits_for_the_plug_charge_grant_instead_of_ending(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, _clock):
            c = s.jobs
            values = c.snapshot()
            await c.create(request(), revision=values["revision"], scope=values["scope"])
            await c.tick()
            assert c.job.state == "charging"
            # Virtual Plug & Charge is switched on, as after an app restart:
            # the gate closes the release until Home Assistant grants it.
            await s.control.async_configure_plug_charge(True, policy="")
            await s.refresh()
            sample = s._job_inputs()
            assert (sample.permitted, sample.release_waiting) == (False, True)
            c.observe()
            assert c.job.state == "charging"
            assert c.stop_requested()
            writes = len(transport.writes)
            await c.tick()
            assert len(transport.writes) == writes
            # The release switched off by hand still ends the order.
            s.control.charging_release_requested = False
            assert not s._job_inputs().release_waiting
            c.observe()
            assert (c.job.state, c.job.reason) == ("interrupted", "independent_stop")
    asyncio.run(run())


def test_home_assistant_grants_the_release_an_order_waits_for(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            c = s.jobs
            values = c.snapshot()
            await c.create(request(), revision=values["revision"], scope=values["scope"])
            await c.tick()
            clock[0] += 1000
            await s.control.async_configure_plug_charge(True, policy="")
            clock[0] += 10
            await s.refresh()
            await c.tick()
            assert (c.job.state, current_writes(transport)[-1]) == ("charging", 1)
            # Only the gate waits: the order does not refuse Home Assistant its
            # grant, a target's stop still would.
            assert c.stop_requested()
            assert not s.control.release_stop_requested
            clock[0] += 1000
            await s.control.async_authorize_plug_charge(
                s.control.plug_charge["session"], deadline=datetime.now(UTC) + timedelta(seconds=10),
            )
            assert transport.bank[R.CHARGING_RELEASE.address] == 1
            # Until a poll shows the open release, the order keeps waiting.
            assert s._job_inputs().release_waiting
            c.observe()
            assert c.job.state == "charging"
            clock[0] += 10
            await s.refresh()
            assert s._job_inputs().permitted
            await c.tick()
            assert (c.job.state, current_writes(transport)[-1]) == ("charging", 12)
    asyncio.run(run())


@pytest.mark.parametrize("block", [R.CABLE_LOCK_STATUS.address, R.ERROR_CODE.address])
def test_one_lost_reply_of_the_wallbox_status_does_not_end_an_order(tmp_path, block):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            c = s.jobs
            values = c.snapshot()
            await c.create(request(), revision=values["revision"], scope=values["scope"])
            await c.tick()
            writes = current_writes(transport)
            clock[0] += 10
            # An exception reply: the poll holds the block's values.
            transport.read_exceptions[block] = 4
            await s.refresh()
            assert not s._job_inputs().status_known
            await c.tick()
            assert (c.job.state, current_writes(transport)) == ("charging", writes)
            transport.read_exceptions.pop(block)
            clock[0] += 10
            await s.refresh()
            await c.tick()
            assert (c.job.state, current_writes(transport)) == ("charging", writes)
    asyncio.run(run())
