"""Energy ceilings retain command ordering and never turn arming into a start."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import (
    ConnectionState,
    WallboxData,
)
from custom_components.mennekes_amtron_rtu.device.energy_goal import EnergyGoal
from custom_components.mennekes_amtron_rtu.device.energy_goal_controller import (
    EnergyGoalController,
)
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
    StorageStateUnknownError,
)
from custom_components.mennekes_amtron_rtu.device.poller import DevicePoller
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.fakes import put, raise_connection_error
from tests.test_control import Harness
from tests.test_goal_store import store_at
from tests.test_modbus_service import current_writes, service_fixture


async def _runner(*, paused=False, read_only=False, persist=None, notify=None):
    harness = Harness(
        read_only=read_only, state_change_interval_seconds=300,
        values={"charging_current_ems": 1.0 if paused else 16.0, "evse_state": EvseState.CHARGING},
    )
    control = await harness.connect()
    control.pause_requested = paused
    control.charging_setpoint = 10
    persisted = []

    async def save():
        persisted.append(control.energy_goal.snapshot())

    runner = EnergyGoalController(
        control, harness.client, persist or save, notify, sleep=harness._sleep,
    )
    return harness, control, runner, persisted


def _snapshot(harness, total, *, energy_read=True, generation=None):
    return WallboxData(
        values={"energy_total": total}, energy_read=energy_read,
        energy_goal_generation=(harness.client.write_state.energy_goal_generation if generation is None else generation),
    )


async def _settle(runner):
    if runner._task is not None:
        await asyncio.wait_for(runner._task, 1)


@pytest.mark.parametrize("paused", [False, True])
def test_arming_and_disabling_only_change_the_energy_ceiling(paused):
    async def run():
        harness, control, runner, saved = await _runner(paused=paused)
        await control.async_set_energy_goal(12)
        assert control.energy_goal.target_kwh == 12
        assert control.energy_goal.baseline_kwh == 1234.5
        assert control.pause_requested is paused
        assert control.charging_setpoint == 10
        assert harness.transport.writes == []
        assert saved == [control.energy_goal.snapshot()]
        reads = list(harness.transport.reads)
        await runner.async_set_target(0)
        assert harness.transport.reads == reads
        assert control.energy_goal.status == "disabled"
        assert control.pause_requested is paused
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("value", [0.15, -1, True, float("nan"), 1001])
def test_invalid_target_never_reads_or_replaces_goal(value):
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        original = control.energy_goal.snapshot()
        reads = list(harness.transport.reads)
        with pytest.raises(DeviceValidationError, match="energy_goal_invalid"):
            await runner.async_set_target(value)
        assert control.energy_goal.snapshot() == original
        assert harness.transport.reads == reads
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("invalid_read", [False, True])
def test_failed_meter_read_retains_old_amount_but_latches_counter_failure(invalid_read):
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        original = control.energy_goal.snapshot()
        if invalid_read:
            put(harness.transport.bank, R.ENERGY_TOTAL, float("nan"))
        else:
            harness.transport.read_exceptions[R.ENERGY_TOTAL.address] = raise_connection_error()
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await runner.async_set_target(20)
        assert control.energy_goal.target_kwh == original["target_kwh"]
        assert runner.stop_requested
        await _settle(runner)
        assert control.energy_goal.status == "meter_unavailable"
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
    asyncio.run(run())


def test_goal_anchor_rechecks_physical_identity_even_without_reconnect():
    async def run():
        harness, control, runner, _saved = await _runner()
        harness.client.bind_identity("ABC123456789")
        await runner.async_set_target(12)
        assert harness.client.identity_verified
        put(harness.transport.bank, R.SERIAL_NUMBER, "DIFFERENTBOX")
        put(harness.transport.bank, R.ENERGY_TOTAL, 10)
        before = list(harness.transport.reads)
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await runner.async_set_target(20)
        assert control.energy_goal.baseline_kwh == 1234.5
        assert control.energy_goal.target_kwh == 12
        assert not harness.client.identity_verified
        assert (R.ENERGY_TOTAL.address, R.ENERGY_TOTAL.count) not in harness.transport.reads[len(before):]
        await runner.async_stop()
        assert harness.transport.writes == []
    asyncio.run(run())


def test_reached_goal_persists_before_pause_and_bypasses_only_the_long_wait():
    async def run():
        harness, control, runner, saved = await _runner()
        await runner.async_set_target(0.1)
        await control.async_set_charging_current(12)
        assert current_writes(harness.transport) == [12]
        harness.client.write_state.mode_change_at = harness.clock[0]
        runner.observe(_snapshot(harness, 1234.6))
        await _settle(runner)
        assert control.energy_goal.status == "reached"
        assert saved[-1]["status"] == "reached"
        assert current_writes(harness.transport) == [12, 1]
        assert harness.slept == [5]
        assert control.pause_requested is True
        assert control.charging_setpoint == 12
        assert harness.client.write_state.mode_change_at == harness.clock[0]
        runner.observe(_snapshot(harness, 1234.7))
        await _settle(runner)
        assert current_writes(harness.transport) == [12, 1]
        await runner.async_stop()
        await control.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("fresh_read", ["running", "unreadable"])
def test_reached_goal_does_not_trust_an_old_paused_poll(fresh_read):
    async def run():
        harness, control, runner, _saved = await _runner(paused=True)
        poller = DevicePoller(
            harness.client, harness.identity, connection_state=ConnectionState(),
            monotonic=lambda: harness.clock[0],
        )
        await runner.async_set_target(0.1)
        harness.snapshot = await poller.async_poll(WallboxData(), last_success=True)
        assert control.is_paused is True

        # The device changes, but the next poll cannot read current_limits.
        # Keep the old owner snapshot while the goal processes the new poll,
        # as can happen while HA awaits an optional session observer.
        put(harness.transport.bank, R.CHARGING_CURRENT_EMS, 16)
        put(harness.transport.bank, R.ENERGY_TOTAL, 1234.8)
        harness.transport.read_exceptions[R.DOWNGRADE_CURRENT.address] = 0x04
        if fresh_read == "unreadable":
            harness.transport.read_exceptions[R.CHARGING_CURRENT_EMS.address] = 0x04
        harness.clock[0] += 60
        observed = await poller.async_poll(harness.snapshot, last_success=True)
        assert "current_limits" in observed.failed_blocks
        assert observed.get("charging_current_ems") is None
        runner.observe(observed)
        await _settle(runner)
        assert control.energy_goal.status == "reached"
        assert current_writes(harness.transport) == [1]
        assert harness.client.write_state.mode_change_at == harness.clock[0]
        assert control.charging_setpoint == (16 if fresh_read == "running" else 10)
        harness.transport.read_exceptions.clear()
        assert await harness.client.async_read_register(R.CHARGING_CURRENT_EMS) == 1
        await runner.async_stop()
        await control.async_shutdown()

    asyncio.run(run())


def test_reached_goal_confirms_current_pause_without_an_unneeded_write():
    async def run():
        harness, control, runner, _saved = await _runner(paused=True)
        await runner.async_set_target(0.1)
        harness.transport.reads.clear()
        runner.observe(_snapshot(harness, 1234.8))
        await _settle(runner)
        assert control.energy_goal.status == "reached"
        assert (R.CHARGING_CURRENT_EMS.address, R.CHARGING_CURRENT_EMS.count) in harness.transport.reads
        assert current_writes(harness.transport) == []
        assert control.pause_requested is True
        await runner.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("applied", [False, True])
def test_uncertain_goal_pause_checks_actual_state_before_retrying(applied):
    async def run():
        harness, control, runner, _saved = await _runner(paused=True)
        await runner.async_set_target(0.1)
        put(harness.transport.bank, R.CHARGING_CURRENT_EMS, 16)
        original = harness.transport.write_registers

        async def unconfirmed_write(address, values, **kwargs):
            if applied:
                await original(address, values, **kwargs)
            raise raise_connection_error()

        harness.transport.write_registers = unconfirmed_write
        runner.observe(_snapshot(harness, 1234.8))
        await _settle(runner)
        assert control.energy_goal.status == "reached"
        assert control.pause_requested is True
        assert control.charging_setpoint == 16
        assert current_writes(harness.transport) == ([1] if applied else [])
        harness.transport.write_registers = original

        # If the old write reached the wallbox, confirmation avoids a second
        # write. Otherwise the idempotent retry still observes the 5-s floor.
        harness.clock[0] += 1
        runner.observe(_snapshot(harness, 1234.8, energy_read=False))
        await _settle(runner)
        assert current_writes(harness.transport) == [1]
        assert harness.slept == ([] if applied else [4])
        assert control.pause_requested is True
        await runner.async_stop()
        await control.async_shutdown()

    asyncio.run(run())


def test_cached_and_pre_arm_polls_cannot_erase_or_complete_a_goal():
    async def run():
        harness, control, runner, saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(_snapshot(harness, 1247, energy_read=False))
        runner.observe(_snapshot(harness, 0, generation=0))
        assert runner._task is None
        assert control.energy_goal.status == "active"
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        assert len(saved) == 1
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("first", [None, 1247, 1236])
def test_evidence_waiting_for_command_lock_is_sticky(first):
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        async with control.energy_goal_guard:
            runner.observe(None if first is None else _snapshot(harness, first))
            runner.observe(_snapshot(harness, 1235))
            runner.observe(_snapshot(harness, 1235, energy_read=False))
            assert control.energy_goal.status == "active"
        await _settle(runner)
        assert control.energy_goal.status == (
            "meter_unavailable" if first is None else "reached" if first == 1247 else "meter_reset"
        )
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
    asyncio.run(run())


def test_new_goal_discards_old_generation_waiting_observations():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        async with control.energy_goal_guard:
            runner.observe(_snapshot(harness, 1247))
            harness.client.write_state.energy_goal = EnergyGoal()
            harness.client.write_state.energy_goal_generation += 1
        await _settle(runner)
        assert control.energy_goal.status == "disabled"
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


def test_waiting_resume_is_fenced_by_terminal_evidence_before_worker_gets_lock():
    async def run():
        harness, control, runner, _saved = await _runner(paused=True)
        await runner.async_set_target(12)
        async with control.energy_goal_guard:
            resume = asyncio.create_task(control.async_resume_charging())
            await asyncio.sleep(0)
            runner.observe(_snapshot(harness, 1247))
            assert runner.stop_requested
            assert control.energy_goal.status == "active"
        with pytest.raises(ServiceValidationError) as error:
            await resume
        assert error.value.translation_key == "energy_goal_stop_active"
        await _settle(runner)
        assert control.energy_goal.status == "reached"
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


def test_waiting_recovery_preserves_pending_energy_stop():
    async def run():
        harness, control, runner, _saved = await _runner()
        put(harness.transport.bank, R.ERROR_CODE, 200)
        await runner.async_set_target(12)
        async with control.energy_goal_guard:
            recovery = asyncio.create_task(control.async_recover_from_error())
            await asyncio.sleep(0)
            runner.observe(_snapshot(harness, 1247))
        assert await recovery
        await _settle(runner)
        assert current_writes(harness.transport) == [1]
        assert control.pause_requested is True
        assert control.energy_goal.status == "reached"
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("action", ["resume", "release", "unlock"])
def test_terminal_evidence_during_guarded_reads_is_rechecked_before_start(action):
    async def run():
        harness, control, runner, _saved = await _runner(paused=True)
        await runner.async_set_target(12)

        async def wait_then_notice_goal():
            runner.observe(_snapshot(harness, 1247))

        control._async_check_transition_interval = wait_then_notice_goal
        method = {
            "resume": control.async_resume_charging,
            "release": lambda: control.async_set_charging_release(True),
            "unlock": lambda: control.async_set_lock_evse(False),
        }[action]
        if action == "release":
            put(harness.transport.bank, R.CHARGING_RELEASE, 0)
        elif action == "unlock":
            put(harness.transport.bank, R.LOCK_EVSE, 1)
        with pytest.raises(ServiceValidationError) as error:
            await method()
        assert error.value.translation_key == "energy_goal_stop_active"
        await _settle(runner)
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


def test_failed_new_target_retains_old_goal_failure_already_waiting_on_lock():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner._persist = AsyncMock(side_effect=OSError("disk full"))
        async with control.energy_goal_guard:
            setter = asyncio.create_task(runner.async_set_target(20))
            await asyncio.sleep(0)
            runner.observe(None)
        with pytest.raises(OSError, match="disk full"):
            await setter
        await _settle(runner)
        assert control.energy_goal.target_kwh == 12
        assert control.energy_goal.status == "meter_unavailable"
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
    asyncio.run(run())


def test_failed_target_save_rolls_back_and_invalidates_interleaved_polls():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        original = control.energy_goal.snapshot()

        async def failing():
            raise OSError("disk full")

        runner._persist = failing
        with pytest.raises(OSError, match="disk full"):
            await runner.async_set_target(20)
        await _settle(runner)
        assert control.energy_goal.snapshot() == original
        assert harness.client.write_state.energy_goal_generation == 3
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


def test_cancelled_save_keeps_the_target_that_may_already_be_committed():
    async def run():
        saved, notifications = [], []
        harness, control, runner, _saved = await _runner(notify=lambda: notifications.append(True))

        async def committed_then_cancelled():
            saved.append(control.energy_goal.snapshot())
            raise asyncio.CancelledError

        runner._persist = committed_then_cancelled
        with pytest.raises(asyncio.CancelledError):
            await runner.async_set_target(12)
        assert control.energy_goal.snapshot() == saved[-1]
        assert notifications == [True]
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


def test_cancelled_committed_target_invalidates_queued_timed_command():
    async def run():
        harness, control, runner, _saved = await _runner()
        control.soc_goal.configure_source("sensor.car_soc")
        control.soc_goal.set_target(80, soc=20)
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        try:
            await control.async_pause_charging()
            await control.async_set_timed_current(
                6, deadline=datetime.now(UTC) + timedelta(hours=1), owner="timed:entry"
            )
            assert control.pending_action == "set_current"

            async def committed_then_cancelled():
                raise asyncio.CancelledError

            runner._persist = committed_then_cancelled
            with pytest.raises(asyncio.CancelledError):
                await runner.async_set_target(12)
            assert control.energy_goal.target_kwh == 12
            assert control.pending_action is None
            assert control.events.snapshot()["events"][-1]["reason"] == "energy_goal"
            assert current_writes(harness.transport) == [1]
        finally:
            release.set()
            await runner.async_stop()
            await control.async_shutdown()

    asyncio.run(run())


def test_uncertain_target_preserves_storage_error_if_queue_notification_fails():
    async def run():
        _harness, control, runner, _saved = await _runner()
        runner._persist = AsyncMock(side_effect=StorageCommitUncertainError("directory sync"))
        control.cancel_goal_resume = lambda: (_ for _ in ()).throw(RuntimeError("listener failed"))
        with pytest.raises(StorageCommitUncertainError, match="directory sync"):
            await runner.async_set_target(12)
        assert control.energy_goal.target_kwh == 12
        await runner.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("target", [0, 8])
def test_unknown_goal_store_fences_and_retries_pause_until_saved(tmp_path, target):
    async def run():
        harness, control, runner, _saved = await _runner()
        store = store_at(tmp_path)
        runner._persist = lambda: store.async_save(control.energy_goal)
        await runner.async_set_target(12)
        write, read = store._write, store._validate_file

        def replace_then_fail(payload):
            write(payload)
            raise OSError("directory sync failed")

        def unreadable():
            raise OSError("readback failed")

        store._write = replace_then_fail
        store._validate_file = unreadable
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = 2
        with pytest.raises(StorageStateUnknownError):
            await runner.async_set_target(target)
        assert control.energy_goal.target_kwh == target
        assert runner.stop_requested
        await _settle(runner)
        assert runner.stop_requested
        assert current_writes(harness.transport) == []
        del harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address]
        # A normal poll must retry even when the requested goal is disabled.
        runner.observe(_snapshot(harness, 1234.5, energy_read=False))
        await _settle(runner)
        assert current_writes(harness.transport) == [1]
        assert runner.stop_requested
        store._write, store._validate_file = write, read
        await runner.async_set_target(target)
        assert not runner.stop_requested
        assert (await store.async_load()).snapshot() == control.energy_goal.snapshot()
        await runner.async_stop()

    asyncio.run(run())


def test_unknown_worker_save_fences_until_a_later_poll_saves():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner._persist = AsyncMock(side_effect=[StorageStateUnknownError("readback failed"), None, None])
        runner.observe(None)
        await _settle(runner)
        assert runner._storage_unknown
        assert current_writes(harness.transport) == [1]
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        assert not runner._storage_unknown
        assert not control.energy_goal.resume_on_recovery
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("retry_fails", [False, True])
def test_shutdown_retries_unknown_disabled_target_save(retry_fails):
    async def run():
        _harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        retry = StorageStateUnknownError("still unreadable") if retry_fails else None
        persist = AsyncMock(side_effect=[StorageStateUnknownError("readback failed"), retry])
        runner._persist = persist
        with pytest.raises(StorageStateUnknownError):
            await runner.async_set_target(0)
        assert runner.stop_requested
        await runner.async_stop()
        assert persist.await_count == 2
        assert control.energy_goal.status == "disabled"
        assert runner.stop_requested is retry_fails

    asyncio.run(run())


def test_unknown_recovery_save_never_resumes_before_storage_recovers():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        assert control.energy_goal.resume_on_recovery
        saving_allowed = False

        async def save():
            if not saving_allowed and control.energy_goal.status == "active" and not control.energy_goal.resume_on_recovery:
                raise StorageStateUnknownError("readback failed")

        runner._persist = save
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        assert runner.stop_requested
        assert current_writes(harness.transport) == [1]
        runner.observe(_snapshot(harness, 1235, energy_read=False))
        await _settle(runner)
        assert runner.stop_requested
        assert current_writes(harness.transport) == [1]
        saving_allowed = True
        runner.observe(_snapshot(harness, 1235, energy_read=False))
        await _settle(runner)
        assert not runner.stop_requested
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("fails", [False, True])
def test_cancelled_target_waiting_for_store_lock_finishes_or_rolls_back(tmp_path, fails):
    async def run():
        _harness, control, runner, _saved = await _runner()
        store = store_at(tmp_path)
        await store.async_save(control.energy_goal)
        old = control.energy_goal.snapshot()
        started = asyncio.Event()

        async def save():
            started.set()
            await store.async_save(control.energy_goal)

        def failed_write(_payload):
            raise OSError("disk failed")

        if fails:
            store._write = failed_write
        runner._persist = save
        async with store._lock:
            task = asyncio.create_task(runner.async_set_target(12))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        with pytest.raises(OSError if fails else asyncio.CancelledError):
            await task
        assert control.energy_goal.snapshot() == (old if fails else (await store.async_load()).snapshot())
        if fails:
            assert (await store.async_load()).snapshot() == old
        else:
            assert control.energy_goal.target_kwh == 12
        await runner.async_stop()

    asyncio.run(run())


async def _assert_service_cancelled_store_wait(tmp_path, *, kind):
    service, _transport, _clock = await service_fixture(tmp_path)
    try:
        if kind == "soc":
            await service.control.async_configure_soc("sensor.soc")
            controller = service.control.soc_goal_controller
        else:
            controller = service.control.goal_controller
        started = asyncio.Event()

        async def save():
            started.set()
            await service._persist()

        controller._persist = save
        async with service._store_lock:
            if kind == "soc":
                action = service.control.async_set_soc_goal(80, source="sensor.soc", soc=20)
            else:
                action = service.control.async_set_energy_goal(12)
            task = asyncio.create_task(action)
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        with pytest.raises(asyncio.CancelledError):
            await task
        restored = WriteState()
        IntentStore(service._store.path, service._store.identity).load(restored)
        if kind == "soc":
            assert restored.soc_goal.snapshot() == service.control.soc_goal.snapshot()
            assert restored.soc_goal.target_percent == 80
        else:
            assert restored.energy_goal.snapshot() == service.control.energy_goal.snapshot()
            assert restored.energy_goal.target_kwh == 12
    finally:
        await service.stop()


def test_cancelled_energy_target_waiting_for_service_store_lock_is_saved(tmp_path):
    asyncio.run(_assert_service_cancelled_store_wait(tmp_path, kind="energy"))


def test_failed_stop_save_still_pauses_and_does_not_spam_logs(caplog):
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner._persist = AsyncMock(side_effect=OSError("private details"))
        for _ in range(2):
            runner.observe(_snapshot(harness, 1247))
            await _settle(runner)
        assert current_writes(harness.transport) == [1]
        assert control.energy_goal.stop_requested
        assert control.pause_requested is True
        assert sum("Cannot enforce energy goal" in record.message for record in caplog.records) == 1
        assert "private details" not in caplog.text
        runner._persist = AsyncMock()
        runner.observe(_snapshot(harness, 1247))
        await _settle(runner)
        assert not runner._failure_logged
        await runner.async_stop()
    asyncio.run(run())


def test_failed_stop_write_retries_only_on_another_observation():
    async def run():
        harness, _control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = 2
        runner.observe(_snapshot(harness, 1247))
        await _settle(runner)
        assert current_writes(harness.transport) == []
        assert harness.diagnostics.writes_rejected == 1
        assert runner._task is None
        harness.transport.write_exceptions.clear()
        harness.clock[0] += 5
        runner.observe(_snapshot(harness, 1247, energy_read=False))
        await _settle(runner)
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("command", ["current", "resume", "release", "unlock"])
def test_latched_stop_rejects_start_commands_until_explicit_goal_change(command):
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(_snapshot(harness, 1247))
        await _settle(runner)
        before = list(harness.transport.writes)
        methods = {
            "current": lambda: control.async_set_charging_current(12),
            "resume": control.async_resume_charging,
            "release": lambda: control.async_set_charging_release(True),
            "unlock": lambda: control.async_set_lock_evse(False),
        }
        with pytest.raises(ServiceValidationError) as error:
            await methods[command]()
        assert error.value.translation_key == "energy_goal_stop_active"
        assert harness.transport.writes == before
        await runner.async_set_target(0)
        assert control.pause_requested is True
        assert harness.transport.writes == before
        await runner.async_stop()
    asyncio.run(run())


def test_stopped_and_unsupported_controllers_refuse_arming():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_stop()
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await runner.async_set_target(12)
        runner.observe(None)
        assert runner._task is None
        harness.identity.layout_version = R.LAYOUT_V01_00
        with pytest.raises(DeviceValidationError, match="energy_goal_not_supported"):
            await EnergyGoalController(control, harness.client, AsyncMock()).async_set_target(12)
        with pytest.raises(ServiceValidationError) as error:
            await control.async_set_energy_goal(12)
        assert error.value.translation_key == "energy_goal_not_supported"
        await control.async_pause_for_energy_goal()
        assert harness.transport.writes == []
    asyncio.run(run())


def test_stopping_while_a_setter_waits_for_the_lock_prevents_a_meter_read():
    async def run():
        harness, control, runner, _saved = await _runner()
        async with control.energy_goal_guard:
            task = asyncio.create_task(runner.async_set_target(12))
            await asyncio.sleep(0)
            stopping = asyncio.create_task(runner.async_stop())
            await asyncio.sleep(0)
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await task
        assert harness.transport.reads == []
        await stopping
    asyncio.run(run())


@pytest.mark.parametrize("save_failed", [False, True])
def test_shutdown_retains_terminal_evidence_queued_behind_another_command(save_failed, caplog):
    async def run():
        harness, control, runner, saved = await _runner()
        await runner.async_set_target(12)
        if save_failed:
            runner._persist = AsyncMock(side_effect=OSError("private details"))
        async with control.energy_goal_guard:
            runner.observe(None)
            stopping = asyncio.create_task(runner.async_stop())
            await asyncio.sleep(0)
        await stopping
        assert control.energy_goal.status == "meter_unavailable"
        assert control.pause_requested is True
        assert harness.transport.writes == []
        if save_failed:
            assert "Cannot save energy goal stop on shutdown" in caplog.text
            assert "private details" not in caplog.text
        else:
            assert saved[-1]["status"] == "meter_unavailable"
    asyncio.run(run())


def test_read_only_owner_rejects_targets_without_bus_traffic():
    async def run():
        harness, _control, runner, _saved = await _runner(read_only=True)
        with pytest.raises(DeviceValidationError, match="read_only_mode"):
            await runner.async_set_target(12)
        assert harness.transport.reads == []
        await runner.async_stop()
    asyncio.run(run())


def test_ui_observer_errors_do_not_undo_the_goal(caplog):
    async def run():
        def broken():
            raise RuntimeError("private details")

        _harness, control, runner, _saved = await _runner(notify=broken)
        await runner.async_set_target(12)
        assert control.energy_goal.status == "active"
        assert "Unable to publish an energy goal update" in caplog.text
        assert "private details" not in caplog.text
        await runner.async_stop()
    asyncio.run(run())


def test_service_goal_is_persisted_reloaded_and_enforced_without_ha(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            result = await service.command({
                "command": "energy_goal", "value": 12,
                "expected_serial_number": "ABC123456789",
            })
            assert result["control"]["supports_energy_goal"]
            assert result["control"]["energy_goal"]["status"] == "active"
            assert current_writes(transport) == []
            saved = json.loads((tmp_path / "intent.json").read_text())
            assert saved["energy_goal"]["baseline_kwh"] == 1234.5
        finally:
            await service.stop()
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            assert service.control.energy_goal.status == "active"
            put(transport.bank, R.ENERGY_TOTAL, 1247)
            service._poller._slow_read_at = None
            await service.refresh()
            await _settle(service.goal)
            assert service.control.energy_goal.status == "reached"
            assert current_writes(transport) == [1]
            assert json.loads((tmp_path / "intent.json").read_text())["energy_goal"]["status"] == "reached"
        finally:
            await service.stop()
    asyncio.run(run())


def test_store_rejects_corrupt_goal_before_replacing_existing_intent(tmp_path):
    state = WriteState(pause_requested=True, charging_setpoint=8)
    store = IntentStore(tmp_path / "intent.json", "ABC")
    store.save(state)
    payload = json.loads(store.path.read_text())
    payload["pause_requested"] = False
    payload["energy_goal"] = {"target_kwh": 12, "status": "active"}
    store.path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="energy goal"):
        store.load(state)
    assert state.pause_requested is True
    assert state.charging_setpoint == 8


@pytest.mark.parametrize("paused", [False, True])
def test_meter_recovery_resumes_only_the_goal_owned_pause(paused):
    async def run():
        harness, control, runner, saved = await _runner(paused=paused)
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        assert control.energy_goal.resume_on_recovery is (not paused)
        assert control.pause_requested is True
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        if control._flush_task is not None:
            await asyncio.wait_for(control._flush_task, 1)
        assert control.energy_goal.status == "active"
        assert not control.energy_goal.resume_on_recovery
        assert current_writes(harness.transport) == ([] if paused else [1, 16])
        assert all(row["target_kwh"] == 12 for row in saved)
        await runner.async_stop()
        await control.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("retry", ["poll", "shutdown"])
def test_meter_recovery_retries_failed_persistence_without_auto_resume(retry):
    async def run():
        harness, control, runner, saved = await _runner(paused=True)
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        assert saved[-1]["status"] == "meter_unavailable"

        async def fail_once():
            runner._persist = save
            raise OSError("disk full")

        async def save():
            saved.append(control.energy_goal.snapshot())

        runner._persist = fail_once
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        assert control.energy_goal.status == "active"
        assert saved[-1]["status"] == "meter_unavailable"

        if retry == "poll":
            runner.observe(_snapshot(harness, 1235, energy_read=False))
            await _settle(runner)
        else:
            await runner.async_stop()
        assert saved[-1]["status"] == "active"
        assert current_writes(harness.transport) == []
        await runner.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["pause", "release", "lock"])
def test_explicit_command_revokes_outage_recovery_before_meter_returns(action):
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        assert control.energy_goal.resume_on_recovery
        if action == "pause":
            await control.async_pause_charging()
        elif action == "release":
            await control.async_set_charging_release(False)
        else:
            await control.async_set_lock_evse(True)
        assert not control.energy_goal.resume_on_recovery
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        await control.async_shutdown()
        assert all(value == 1 for value in current_writes(harness.transport))
        await runner.async_stop()
    asyncio.run(run())


def test_outage_recovery_waits_for_wallbox_error_to_clear():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        put(harness.transport.bank, R.ERROR_CODE, 200)
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        assert control.energy_goal.status == "active"
        assert control.energy_goal.resume_on_recovery
        assert current_writes(harness.transport) == [1]
        put(harness.transport.bank, R.ERROR_CODE, 0)
        runner.observe(_snapshot(harness, 1235, energy_read=False))
        await _settle(runner)
        if control._flush_task is not None:
            await asyncio.wait_for(control._flush_task, 1)
        assert current_writes(harness.transport) == [1, 16]
        await runner.async_stop()
        await control.async_shutdown()
    asyncio.run(run())


def test_queued_manual_pause_before_outage_worker_prevents_recovery_grant():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        async with control.energy_goal_guard:
            pausing = asyncio.create_task(control.async_pause_charging())
            await asyncio.sleep(0)
            runner.observe(None)
        await pausing
        await _settle(runner)
        assert not control.energy_goal.resume_on_recovery
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        await control.async_shutdown()
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["disable", "new_goal", "manual_pause", "soc_reached"])
def test_automatic_resume_waiting_for_vehicle_guard_can_be_revoked(change):
    async def run():
        harness, control, runner, _saved = await _runner()
        waiting = asyncio.Event()
        release = asyncio.Event()
        async def hold(delay):
            waiting.set()
            await release.wait()
            harness.clock[0] += delay
        control._sleep = hold
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        await waiting.wait()
        assert control.pending_action == "resume"
        if change == "disable":
            await runner.async_set_target(0)
        elif change == "new_goal":
            await runner.async_set_target(20)
        elif change == "manual_pause":
            await control.async_pause_charging()
        else:
            control.soc_goal.configure_source("sensor.soc|")
            control.soc_goal.set_target(80, 80)
        release.set()
        if control._flush_task is not None:
            await asyncio.wait_for(control._flush_task, 1)
        assert all(value == 1 for value in current_writes(harness.transport))
        await runner.async_stop()
        await control.async_shutdown()
    asyncio.run(run())



def test_manual_pause_cannot_be_undone_by_old_recovery_grant_after_failed_save_and_restart():
    async def run():
        _harness, control, runner, saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        assert control.energy_goal.resume_on_recovery
        durable = saved[-1]
        runner._persist = AsyncMock(side_effect=OSError("disk full"))
        await control.async_pause_charging()
        assert not control.energy_goal.resume_on_recovery
        assert "resume_on_recovery" not in durable
        await runner.async_stop()
        await control.async_shutdown()
        cold_harness, cold_control, cold_runner, _saved = await _runner(paused=True)
        cold_control.write_state.energy_goal = EnergyGoal.restore(durable)
        cold_runner.observe(_snapshot(cold_harness, 1235))
        await _settle(cold_runner)
        assert cold_control.energy_goal.status == "active"
        assert cold_control.pause_requested is True
        assert cold_harness.transport.writes == []
        await cold_runner.async_stop()
    asyncio.run(run())


def test_first_goal_cannot_arm_when_storage_fails():
    async def run():
        harness, control, runner, _saved = await _runner(persist=AsyncMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            await runner.async_set_target(12)
        assert control.energy_goal.status == "disabled"
        assert harness.transport.writes == []
        await runner.async_stop()
    asyncio.run(run())


def test_failed_disable_preserves_active_goal_and_meter_failure_during_save():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        async def failed():
            runner.observe(None)
            raise OSError("disk full")
        runner._persist = failed
        with pytest.raises(OSError, match="disk full"):
            await runner.async_set_target(20)
        runner._persist = AsyncMock()
        await _settle(runner)
        assert control.energy_goal.status == "meter_unavailable"
        assert current_writes(harness.transport) == [1]
        runner._persist = AsyncMock(side_effect=OSError("disk full"))
        with pytest.raises(OSError, match="disk full"):
            await runner.async_set_target(0)
        runner._persist = AsyncMock()
        await _settle(runner)
        assert control.energy_goal.target_kwh == 12
        await runner.async_stop()
    asyncio.run(run())


def test_new_percentage_stop_during_recovery_persistence_prevents_start():
    async def run():
        harness, control, runner, _saved = await _runner()
        await runner.async_set_target(12)
        runner.observe(None)
        await _settle(runner)
        async def save():
            if control.energy_goal.status == "active" and not control.energy_goal.resume_on_recovery:
                control.soc_goal.configure_source("sensor.car|")
                control.soc_goal.set_target(80, 80)
        runner._persist = save
        runner.observe(_snapshot(harness, 1235))
        await _settle(runner)
        assert control.soc_goal.stop_requested
        assert current_writes(harness.transport) == [1]
        await runner.async_stop()
    asyncio.run(run())
