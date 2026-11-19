"""A stop that waits for the state-change interval is not lost on the way."""

from __future__ import annotations

import asyncio
import struct
from datetime import UTC, datetime, timedelta

from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.plug_charge import PlugChargeState
from tests.fakes import raise_connection_error
from tests.test_control import Harness, R


async def charging(**options):
    h = Harness(
        state_change_interval_seconds=300,
        values={"charging_current_ems": 16.0, "evse_state": EvseState.CHARGING}, **options,
    )
    control = await h.connect()
    h.transport.bank[R.EVSE_STATE.address] = int(EvseState.CHARGING)
    h.transport.bank[R.CP_STATE.address] = int(CpState.C2)
    return h, control


def currents(h):
    return [round(struct.unpack(">f", struct.pack(">HH", *words))[0], 2) for words in h.written(R.CHARGING_CURRENT_EMS)]


def events(control):
    return [(event["outcome"], event["action"]) for event in control.events.snapshot()["events"]]


def test_a_waiting_pause_is_written_again_after_a_reply_that_did_not_come():
    async def run():
        h, control = await charging()
        h.client.write_state.mode_change_at = h.clock[0]
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        h.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = raise_connection_error()
        h.clock[0] += 301
        control._pending_changed.set()
        await asyncio.sleep(0.05)
        # The write failed without an answer: the stop still waits.
        assert control.pending_action == "pause"
        del h.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address]
        h.clock[0] += 6
        control._pending_changed.set()
        await asyncio.wait_for(control._flush_task, 5)
        assert control.pending_action is None
        assert currents(h)[-1] < 6
        await control.async_shutdown()

    asyncio.run(run())


def test_a_waiting_pause_outlasts_a_current_that_switches_nothing():
    async def run():
        h, control = await charging()
        h.client.write_state.mode_change_at = h.clock[0]
        await control.async_pause_charging()
        h.clock[0] += 10
        # A surplus adjustment: written, and the pause keeps its place.
        await control.async_set_charging_current(10)
        assert (control.pending_action, currents(h)) == ("pause", [10.0])
        assert ("replaced", "pause") not in events(control)
        # One that comes too soon after the last write is remembered only.
        await control.async_set_charging_current(12)
        assert (control.pending_action, currents(h), control.charging_setpoint) == ("pause", [10.0], 12)
        h.clock[0] += 300
        control._pending_changed.set()
        await asyncio.wait_for(control._flush_task, 5)
        assert currents(h)[-1] < 6
        await control.async_shutdown()

    asyncio.run(run())


def test_a_waiting_lock_outlasts_a_latched_stop():
    async def run():
        h, control = await charging()
        control.time_limit_reached = lambda: True
        await control.async_pause_for_energy_goal()
        h.clock[0] += 20
        await control.async_set_lock_evse(True)
        assert control.pending_action == "lock"
        # The stop is enforced again with every poll.
        for _ in range(3):
            h.clock[0] += 5
            await control.async_pause_for_energy_goal()
        assert control.pending_action == "lock"
        assert ("cancelled", "lock") not in events(control)
        h.clock[0] += 300
        control._pending_changed.set()
        await asyncio.wait_for(control._flush_task, 5)
        assert h.written(R.LOCK_EVSE) == [[1]]
        await control.async_shutdown()

    asyncio.run(run())


def test_recovery_leaves_a_release_closed_that_nobody_decided():
    async def run():
        h = Harness(error_code=200, values={"charging_current_ems": 16.0, "evse_state": EvseState.CHARGING})
        control = await h.connect()
        h.transport.bank[R.CHARGING_RELEASE.address] = 0
        assert control.charging_release_requested is None
        await control.async_recover_from_error()
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await control.async_shutdown()

    asyncio.run(run())


def test_a_manual_release_ends_with_a_long_unknown_vehicle_state():
    """Another car may have been plugged in meanwhile."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    state = PlugChargeState(enabled=True)
    state.observe(True, observed_at=start)
    state.manual, state.hold = True, False
    for step in range(1, 121):
        state.observe(None, observed_at=start + timedelta(seconds=5 * step))
    state.observe(True, observed_at=start + timedelta(seconds=700))
    assert (state.manual, state.blocked) == (False, True)


def _rate_limited(h):
    h.client.write_state.mode_change_at = h.clock[0]


def test_a_waiting_lock_is_not_taken_by_a_pause_that_must_wait_too():
    import pytest

    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )

    async def go():
        h, control = await charging()
        _rate_limited(h)
        await control.async_set_lock_evse(True)
        with pytest.raises(AmtronRateLimitedError):
            await control.async_pause_charging()
        assert control.pending_action == "lock"
        assert ("replaced", "lock") not in events(control)
        await control.async_shutdown()

    asyncio.run(go())


def test_a_waiting_lock_is_not_taken_by_another_stop():
    import pytest

    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )

    async def go():
        h, control = await charging()
        _rate_limited(h)
        await control.async_set_lock_evse(True)
        with pytest.raises(AmtronRateLimitedError):
            await control.async_set_charging_release(False)
        assert control.pending_action == "lock"
        await control.async_shutdown()

    asyncio.run(go())


def test_a_waiting_pause_outlasts_a_switch_that_changes_nothing():
    async def go():
        h, control = await charging()
        _rate_limited(h)
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        h.clock[0] += 10
        await control.async_set_lock_evse(False)
        assert control.pending_action == "pause"
        await control.async_shutdown()

    asyncio.run(go())


def test_a_newer_decision_of_the_same_owner_replaces_its_waiting_pause():
    async def go():
        h, control = await charging()
        control.soc_goal.status = "active"
        control.soc_goal.target_percent = 80
        _rate_limited(h)
        deadline = datetime.now(UTC) + timedelta(hours=1)
        await control.async_set_timed_current(None, deadline=deadline, owner="departure")
        assert control.pending_action == "pause"
        h.clock[0] += 10
        await control.async_set_timed_current(10, deadline=deadline, owner="departure")
        assert control.pending_action != "pause"
        await control.async_shutdown()

    asyncio.run(go())
