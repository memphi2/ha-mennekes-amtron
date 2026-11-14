"""Changed limits apply without a restart and never leave a current above the cap."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import service_runtime as runtime
from custom_components.mennekes_amtron_rtu.device.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.device.control import ServiceChargePolicy
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.enums import (
    EvseState,
    RequestedPhases,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from tests.test_console_registers import console_fixture
from tests.test_control import Harness


def currents(harness):
    return [decode_register(R.CHARGING_CURRENT_EMS, words) for words in harness.written(R.CHARGING_CURRENT_EMS)]


def capped(control, cap):
    return replace(control.options, current_cap=cap)


def test_read_only_mode_still_needs_a_restart():
    async def run():
        control = await Harness().connect()
        with pytest.raises(ValueError, match="read_only"):
            await control.async_set_limits(replace(control.options, read_only=True))
        assert not control.read_only
    asyncio.run(run())


def test_new_commands_use_a_lowered_or_raised_cap_at_once():
    async def run():
        harness = Harness(current_cap=16)
        control = await harness.connect()
        await control.async_set_limits(capped(control, 10))
        assert control.max_charging_current == 10
        await control.async_set_charging_current(16)
        harness.clock[0] += 10
        # A raised cap still stops at the wallbox's own maximum.
        await control.async_set_limits(capped(control, 32))
        assert control.max_charging_current == 16
        await control.async_set_charging_current(16)
        return currents(harness)
    assert asyncio.run(run()) == [10, 16]


@pytest.mark.parametrize(("interval", "done_at"), [(30, 1030), (600, 1600)])
def test_a_queued_start_is_timed_again_with_a_changed_interval(interval, done_at):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        await control.async_set_limits(replace(control.options, state_change_interval_seconds=interval))
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == done_at
        assert control.pending_action is None
        assert currents(harness)[-1] == 16
    asyncio.run(run())


def test_an_unchanged_interval_leaves_the_queued_start_alone():
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        due, until = control._pending_due, control.pending_until
        await control.async_set_limits(capped(control, 10))
        assert (control._pending_due, control.pending_until) == (due, until)
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1300
        # The queued start runs with the cap in force when it runs.
        assert currents(harness)[-1] == 10
    asyncio.run(run())


@pytest.mark.parametrize("before_pause", [True, False])
def test_a_running_phase_switch_wait_keeps_its_length(before_pause):
    async def run():
        harness = Harness(values={"evse_state": EvseState.CHARGING}, phase_switch_grace_seconds=5)
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        longer = replace(control.options, phase_switch_grace_seconds=20)
        waiting, sleep = asyncio.Event(), control._sleep

        async def observed_sleep(delay):
            if control._phase_change is not None and control._phase_change.ready_at is not None:
                waiting.set()
            await sleep(delay)

        control._sleep = observed_sleep
        if before_pause:
            # The wait has not started yet: it takes the new length.
            await control.async_set_limits(longer)
        start = harness.clock[0]
        harness.transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        harness.transport.bank[R.CP_STATE.address] = 11
        if not before_pause:
            await asyncio.wait_for(waiting.wait(), 1)
            await control.async_set_limits(longer)
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        return harness.clock[0] - start
    waited = asyncio.run(run())
    assert waited >= 20 if before_pause else 5 <= waited < 20


@pytest.mark.parametrize(("current", "lowered"), [
    (16.0, True), (10.04, False), (10.0, False), (6.0, False), (1.0, False), (0.0, False),
])
def test_only_a_current_above_the_lowered_cap_is_lowered(current, lowered):
    async def run():
        harness = Harness(current_cap=16, values={"charging_current_ems": current})
        control = await harness.connect()
        await control.async_set_limits(capped(control, 10))
        assert await control.async_enforce_current_cap() is lowered
        assert currents(harness) == ([10] if lowered else [])
        if lowered:
            assert control.charging_setpoint == 10
            assert control.pending_action is None
    asyncio.run(run())


@pytest.mark.parametrize("snapshot", [
    WallboxData(values={"charging_current_ems": 16.0}, held=frozenset({"charging_current_ems"})),
    WallboxData(values={"charging_current_ems": 16.0}, current_write_generation=-1),
    WallboxData(values={"charging_current_ems": True}),
    WallboxData(values={"charging_current_ems": float("nan")}),
    WallboxData(),
])
def test_only_a_fresh_reading_of_this_connection_counts(snapshot):
    async def run():
        harness = Harness(current_cap=16)
        control = await harness.connect()
        await control.async_set_limits(capped(control, 10))
        harness.snapshot = snapshot
        assert await control.async_enforce_current_cap() is False
        assert currents(harness) == []
    asyncio.run(run())


@pytest.mark.parametrize("fence", ["read_only", "pending", "service_order", "order_stop"])
def test_owners_and_stops_keep_their_say(fence):
    async def run():
        harness = Harness(current_cap=16, read_only=fence == "read_only", state_change_interval_seconds=300)
        control = await harness.connect()
        await control.async_set_limits(capped(control, 10))
        if fence == "pending":
            await control.async_pause_charging()
            await control.async_resume_charging()
            assert control.pending_action == "resume"
            harness.snapshot = WallboxData(values={"charging_current_ems": 16.0})
        elif fence == "service_order":
            # A holding order follows the cap on its own tick.
            control.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: False, lambda: False))
        elif fence == "order_stop":
            control.set_service_policy(ServiceChargePolicy(lambda _owner, _enabling: None, lambda: True))
        writes = len(harness.transport.writes)
        assert await control.async_enforce_current_cap() is False
        assert len(harness.transport.writes) == writes
    asyncio.run(run())


def test_a_lowering_within_the_write_floor_is_queued_not_dropped():
    async def run():
        harness = Harness(current_cap=16, values={"charging_current_ems": 8.0})
        control = await harness.connect()
        await control.async_set_charging_current(16)
        # A reading from before our own write is not fresh; this one is.
        harness.snapshot = WallboxData(
            values={"charging_current_ems": 16.0},
            current_write_generation=harness.client.write_state.current_write_generation,
        )
        await control.async_set_limits(capped(control, 10))
        assert await control.async_enforce_current_cap() is True
        assert control.pending_action == "set_current"
        await asyncio.wait_for(control._flush_task, 1)
        assert currents(harness) == [16, 10]
        assert harness.clock[0] == 1005
    asyncio.run(run())


def service_with(control, *, read_only=False):
    service = runtime.ModbusService.__new__(runtime.ModbusService)
    service.control = control
    if control is not None:
        control.read_only = read_only
    service.config = Mock(read_only=read_only)
    service._cap_retry_at = 0.0
    service._command_lock = asyncio.Lock()
    return service


def test_the_service_lowers_after_each_poll_and_backs_off_after_a_failure(caplog, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    control = Mock(max_charging_current=10.0, async_enforce_current_cap=AsyncMock(return_value=True))

    async def run():
        service = service_with(control)
        with caplog.at_level(logging.INFO, logger=runtime.__name__):
            await service._enforce_current_cap()
        assert "lowered to the 10.0 A cap" in caplog.text
        control.async_enforce_current_cap.side_effect = DeviceError("service_job_active")
        await service._enforce_current_cap()
        assert "could not be lowered (service_job_active)" in caplog.text
        assert service._cap_retry_at == 130
        control.async_enforce_current_cap.side_effect = AmtronBusError("lost")
        clock[0] = 129
        await service._enforce_current_cap()
        assert control.async_enforce_current_cap.await_count == 2
        clock[0] = 130
        await service._enforce_current_cap()
        assert "(AmtronBusError)" in caplog.text
        assert control.async_enforce_current_cap.await_count == 3
        await service_with(control, read_only=True)._enforce_current_cap()
        await service_with(None)._enforce_current_cap()
        assert control.async_enforce_current_cap.await_count == 3
    asyncio.run(run())


def test_the_polling_loop_checks_the_cap_after_recovery(tmp_path, monkeypatch):
    class StopLoopError(Exception):
        pass

    async def run():
        service, _transport = await console_fixture(tmp_path)
        calls = []
        try:
            for name in ("refresh", "_recover", "_enforce_current_cap", "_persist"):
                monkeypatch.setattr(service, name, AsyncMock(side_effect=lambda *_a, name=name, **_k: calls.append(name)))

            async def stop(_delay):
                raise StopLoopError

            monkeypatch.setattr(runtime.asyncio, "sleep", stop)
            with pytest.raises(StopLoopError):
                await service._polling_loop()
        finally:
            monkeypatch.undo()
            await service.stop()
        assert calls == ["refresh", "_recover", "_enforce_current_cap", "_persist"]
    asyncio.run(run())
