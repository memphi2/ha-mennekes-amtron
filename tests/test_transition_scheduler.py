"""State transitions share one latest-wins queue and one configurable clock."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import (
    EvseState,
    RequestedPhases,
)
from tests.fakes import put, raise_connection_error
from tests.test_control import Harness


@pytest.mark.parametrize("value", [True, "300", None, -1, 3601, float("nan"), float("inf")])
def test_invalid_transition_options_are_rejected(value):
    with pytest.raises(ValueError, match="state_change_interval_seconds"):
        ControlOptions(state_change_interval_seconds=value)


def test_default_interval_is_300_seconds():
    assert ControlOptions().state_change_interval_seconds == 300


@pytest.mark.parametrize("interval", [0, 30, 300, 3600])
def test_resume_waits_for_configured_interval_without_changing_pause(interval):
    async def run():
        harness = Harness(state_change_interval_seconds=interval)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        due = control.pending_until
        assert control.pending_action == "resume"
        assert control.is_paused is True
        # Repeated identical intent must not extend the deadline or churn DB.
        await control.async_resume_charging()
        assert control.pending_until == due
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1000 + max(5, interval)
        assert control.pending_action is None
        assert control.pending_until is None
        assert control.is_paused is False
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2

    asyncio.run(run())


def test_reasserting_pause_and_recovery_do_not_restart_long_wait():
    async def run():
        harness = Harness(state_change_interval_seconds=300, error_code=200)
        control = await harness.connect()
        await control.async_pause_charging()
        started = control._write_state.mode_change_at
        harness.clock[0] += 10
        await control.async_pause_charging()
        assert control._write_state.mode_change_at == started
        for _ in range(2):
            harness.clock[0] += 10
            await control.async_recover_from_error()
            assert control.is_paused is True
            assert control._write_state.mode_change_at == started
        await control.async_resume_charging()
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1300

    asyncio.run(run())


def test_resume_replaces_pending_phase_request():
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert control.pending_action == "single_phase"
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.written(R.REQUESTED_PHASES) == []
        assert control.is_paused is False

    asyncio.run(run())


def test_phase_switch_and_resume_use_same_guard_without_blocking_internal_pause():
    async def run():
        harness = Harness(
            state_change_interval_seconds=300,
            values={"charging_current_ems": 8, "evse_state": EvseState.CHARGING},
        )
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert control.is_paused is True
        changed = control._write_state.mode_change_at
        harness.clock[0] += 10
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert control._write_state.mode_change_at == changed
        assert control.pending_action is None
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1300
        assert control.charging_setpoint == 8

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["release", "lock"])
def test_alternate_start_paths_queue_and_stop_cancels_them(kind):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        method = control.async_set_charging_release if kind == "release" else control.async_set_lock_evse
        stop = kind == "lock"
        register = R.LOCK_EVSE if stop else R.CHARGING_RELEASE
        await method(stop)
        changed = control._write_state.mode_change_at
        harness.clock[0] += 10
        await method(not stop)
        assert control.pending_action == ("unlock" if stop else "enable_charging")
        await method(stop)
        assert control.pending_action is None
        assert control._write_state.mode_change_at == changed
        assert all(words == [int(stop)] for words in harness.written(register))
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["release", "lock"])
def test_alternate_start_paths_execute_after_interval(kind):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        method = control.async_set_charging_release if kind == "release" else control.async_set_lock_evse
        stop = kind == "lock"
        register = R.LOCK_EVSE if stop else R.CHARGING_RELEASE
        await method(stop)
        await method(not stop)
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.written(register) == [[int(stop)], [int(not stop)]]
        assert harness.clock[0] == 1300

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["release", "lock"])
@pytest.mark.parametrize("failure", ["unreadable", "invalid"])
def test_failed_live_state_read_allows_stop_but_blocks_start(kind, failure):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        method = control.async_set_charging_release if kind == "release" else control.async_set_lock_evse
        stop = kind == "lock"
        register = R.LOCK_EVSE if stop else R.CHARGING_RELEASE
        if failure == "unreadable":
            harness.transport.read_exceptions[register.address] = raise_connection_error()
        else:
            put(harness.transport.bank, register, 7)
        with pytest.raises(ServiceValidationError) as error:
            await method(not stop)
        assert error.value.translation_key == "control_state_unknown"
        await method(stop)
        assert harness.written(register) == [[int(stop)]]

    asyncio.run(run())


def test_queued_command_rechecks_write_permission():
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        control._options = ControlOptions(read_only=True)
        await asyncio.wait_for(control._flush_task, 1)
        assert control.pending_action is None
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1

    asyncio.run(run())


def test_queued_phase_request_rechecks_hardware_support():
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        harness.identity.phase_options_hw = 0
        await asyncio.wait_for(control._flush_task, 1)
        assert control.pending_action is None
        assert harness.written(R.REQUESTED_PHASES) == []

    asyncio.run(run())


def test_queue_recalculates_remaining_time_after_other_transition():
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        # A later uncertain transition extends the shared guard after admission.
        control._write_state.mode_change_at += 10
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1310
        assert harness.slept == [300, 10]
        assert control.pending_action is None

    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError("disk error"), RuntimeError("callback error")])
def test_callback_failure_clears_queue_and_is_not_retried(error):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()

        async def fail():
            raise error

        control._request_refresh = fail
        await asyncio.wait_for(control._flush_task, 1)
        assert control.pending_action is None
        assert control._flush_task is None
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2

    asyncio.run(run())


def test_new_acknowledged_current_supersedes_queue_even_if_persistence_fails():
    async def run():
        harness = Harness()
        control = await harness.connect()
        await control.async_set_charging_current(8)
        await control.async_set_charging_current(16)
        harness.clock[0] += 6

        async def fail():
            raise OSError("state storage unavailable")

        control._request_refresh = fail
        with pytest.raises(OSError, match="state storage unavailable"):
            await control.async_set_charging_current(6)
        assert control.pending_action is None
        await control.async_shutdown()
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2
        assert control.charging_setpoint == 6

    asyncio.run(run())


@pytest.mark.parametrize("stop", ["pause", "release", "lock"])
def test_pending_status_persistence_failure_does_not_prevent_stop(stop):
    async def run():
        harness = Harness()
        control = await harness.connect()
        await control.async_set_charging_current(8)
        await control.async_set_charging_current(16)
        harness.clock[0] += 6

        async def fail():
            raise OSError("state storage unavailable")

        control._request_refresh = fail
        action = (
            control.async_pause_charging() if stop == "pause"
            else control.async_set_charging_release(False) if stop == "release"
            else control.async_set_lock_evse(True)
        )
        with pytest.raises(OSError, match="state storage unavailable"):
            await action
        assert control.pending_action is None
        if stop == "pause":
            assert control.is_paused is True
        else:
            register = R.CHARGING_RELEASE if stop == "release" else R.LOCK_EVSE
            assert harness.written(register) == [[int(stop == "lock")]]
        await control.async_shutdown()

    asyncio.run(run())
