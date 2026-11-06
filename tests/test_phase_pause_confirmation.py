"""A phase command waits for the vehicle, then a cancellable grace period."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import (
    CpState,
    EvseState,
    RequestedPhases,
)
from tests.fakes import put, raise_connection_error
from tests.test_control import Harness


def _harness(*, grace: float = 5, state: EvseState = EvseState.CHARGING) -> Harness:
    harness = Harness(
        phase_switch_grace_seconds=grace,
        values={"charging_current_ems": 8.0, "evse_state": state},
    )
    put(
        harness.transport.bank,
        R.CP_STATE,
        CpState.C2 if state == EvseState.CHARGING else CpState.B1,
    )
    return harness


def _confirm_pause(harness: Harness) -> None:
    put(harness.transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)
    put(harness.transport.bank, R.CP_STATE, CpState.B1)


@pytest.mark.parametrize("grace", [0, 5, 30])
@pytest.mark.parametrize("early_status", ["evse", "cp"])
def test_phase_write_waits_for_vehicle_confirmation_then_configured_grace(grace, early_status):
    async def run():
        harness = _harness(grace=grace)
        control = await harness.connect()

        async def react_after_three_seconds(delay):
            assert harness.written(R.REQUESTED_PHASES) == []
            await harness._sleep(delay)
            if early_status == "evse":
                put(harness.transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)
            else:
                put(harness.transport.bank, R.CP_STATE, CpState.B1)
            if harness.clock[0] >= 1003:
                _confirm_pause(harness)

        control._sleep = react_after_three_seconds
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert control.pending_action == "single_phase"
        assert harness.written(R.REQUESTED_PHASES) == []
        await asyncio.wait_for(control._flush_task, 1)

        assert harness.clock[0] == 1003 + grace
        assert harness.slept[:3] == [1, 1, 1]
        assert sum(harness.slept[3:]) == grace
        assert max(harness.slept) <= 5
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert control.is_paused is True
        assert control.charging_setpoint == 8
        assert control.pending_action is None
        assert control.pending_until is None

    asyncio.run(run())


def test_unresponsive_vehicle_times_out_without_switching_or_repeating_pause(caplog):
    async def run():
        harness = _harness()
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(control._flush_task, 1)

        assert harness.clock[0] == 1030
        assert harness.written(R.REQUESTED_PHASES) == []
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert control.is_paused is True
        assert control.pending_action is None
        assert control.pending_until is None
        assert "phase_pause_timeout" in caplog.text
        # Publish the pause, the pending action and its cancellation, never
        # a new timestamp on each of the thirty readiness probes.
        assert harness.refreshes == 3

    asyncio.run(run())


@pytest.mark.parametrize("cp", [7, CpState.INIT, CpState.E, CpState.F, "unreadable"])
def test_invalid_or_unreadable_cp_aborts_phase_command(cp):
    async def run():
        harness = _harness(state=EvseState.EV_CONNECTED)
        control = await harness.connect()
        if cp == "unreadable":
            harness.transport.read_exceptions[R.CP_STATE.address] = raise_connection_error()
        else:
            put(harness.transport.bank, R.CP_STATE, cp)
        with pytest.raises(ServiceValidationError) as error:
            await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert error.value.translation_key == "phase_pause_state_unknown"
        assert harness.written(R.REQUESTED_PHASES) == []
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert control.is_paused is True
        assert control.pending_action is None
        assert control._phase_change is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "state", [EvseState.NOT_INITIALIZED, EvseState.ERROR, EvseState.SERVICE_MODE]
)
def test_unsafe_initial_evse_state_is_rejected_before_any_write(state):
    async def run():
        harness = _harness(state=state)
        control = await harness.connect()
        with pytest.raises(ServiceValidationError) as error:
            await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert error.value.translation_key == "phase_pause_state_unknown"
        assert harness.transport.writes == []
        assert control.pending_action is None

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["resumed", "evse_error", "cp_unreadable"])
def test_lost_pause_confirmation_during_grace_aborts_phase_command(failure, caplog):
    async def run():
        harness = _harness(state=EvseState.EV_CONNECTED)
        control = await harness.connect()

        async def lose_confirmation(delay):
            await harness._sleep(delay)
            if failure == "resumed":
                put(harness.transport.bank, R.EVSE_STATE, EvseState.CHARGING)
                put(harness.transport.bank, R.CP_STATE, CpState.C2)
            elif failure == "evse_error":
                put(harness.transport.bank, R.EVSE_STATE, EvseState.ERROR)
            else:
                harness.transport.read_exceptions[R.CP_STATE.address] = raise_connection_error()

        control._sleep = lose_confirmation
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1005
        assert harness.written(R.REQUESTED_PHASES) == []
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert control.pause_requested is True
        assert control.pending_action is None
        assert "phase_pause_state_unknown" in caplog.text

    asyncio.run(run())


def test_idle_wallbox_switches_without_pause_or_grace():
    async def run():
        harness = _harness(grace=30, state=EvseState.IDLE)
        put(harness.transport.bank, R.CP_STATE, CpState.A1)
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert harness.written(R.CHARGING_CURRENT_EMS) == []
        assert harness.slept == []
        assert control.pending_action is None

    asyncio.run(run())


def test_layout_101_confirms_evse_pause_without_reading_unavailable_cp():
    async def run():
        harness = _harness(grace=0)
        harness.identity.layout_version = R.LAYOUT_V01_01
        put(harness.transport.bank, R.MODBUS_LAYOUT_VERSION, R.LAYOUT_V01_01)
        harness.transport.read_exceptions[R.CP_STATE.address] = raise_connection_error()
        control = await harness.connect()

        async def confirm_evse_only(delay):
            await harness._sleep(delay)
            put(harness.transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)

        control._sleep = confirm_evse_only
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert harness.clock[0] == 1001
        assert all(address != R.CP_STATE.address for address, _ in harness.transport.reads)

    asyncio.run(run())


def test_new_resume_supersedes_phase_change_during_grace():
    async def run():
        harness = _harness(state=EvseState.EV_CONNECTED)
        control = await harness.connect()
        sleeping = asyncio.Event()
        release_sleep = asyncio.Event()

        async def blocked_sleep(delay):
            sleeping.set()
            await release_sleep.wait()
            await harness._sleep(delay)

        control._sleep = blocked_sleep
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(sleeping.wait(), 1)
        await asyncio.wait_for(control.async_resume_charging(), 1)
        assert control.pending_action == "resume"
        assert control._phase_change is None
        release_sleep.set()
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.written(R.REQUESTED_PHASES) == []
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2
        assert control.is_paused is False
        assert control.pending_action is None

    asyncio.run(run())


def test_shutdown_cancels_phase_change_and_its_grace_wait():
    async def run():
        harness = _harness(state=EvseState.EV_CONNECTED)
        control = await harness.connect()
        sleeping = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked_sleep(_delay):
            sleeping.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        control._sleep = blocked_sleep
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(sleeping.wait(), 1)
        task = control._flush_task
        await asyncio.wait_for(control.async_shutdown(), 1)
        assert cancelled.is_set()
        assert task.done()
        assert control.pending_action is None
        assert control.pending_until is None
        assert control._phase_change is None
        assert harness.written(R.REQUESTED_PHASES) == []
        assert control.pause_requested is True

    asyncio.run(run())


def test_heartbeat_can_write_while_phase_grace_is_waiting():
    async def run():
        harness = _harness(state=EvseState.EV_CONNECTED)
        control = await harness.connect()
        sleeping = asyncio.Event()
        release_sleep = asyncio.Event()

        async def blocked_sleep(delay):
            sleeping.set()
            await release_sleep.wait()
            await harness._sleep(delay)

        control._sleep = blocked_sleep
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(sleeping.wait(), 1)
        await asyncio.wait_for(harness.client.async_write_register(R.HEARTBEAT, 0x55AA), 1)
        assert harness.written(R.HEARTBEAT) == [[0x55AA]]
        assert harness.written(R.REQUESTED_PHASES) == []
        assert control.pending_action == "single_phase"
        release_sleep.set()
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]

    asyncio.run(run())
