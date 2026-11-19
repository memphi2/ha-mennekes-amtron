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


def test_a_lock_whose_reply_did_not_come_is_repeated_without_a_new_interval():
    async def go():
        h, control = await charging()
        _rate_limited(h)
        await control.async_set_lock_evse(True)
        h.transport.write_exceptions[R.LOCK_EVSE.address] = raise_connection_error()
        h.clock[0] += 301
        control._pending_changed.set()
        await asyncio.sleep(0.05)
        assert control.pending_action == "lock"
        del h.transport.write_exceptions[R.LOCK_EVSE.address]
        for _ in range(200):
            await asyncio.sleep(0.005)
            if control._flush_task is None or control._flush_task.done():
                break
        assert control.pending_action is None
        assert events(control)[-1] == ("executed", "lock")
        await control.async_shutdown()

    asyncio.run(go())


def test_a_waiting_lock_outlasts_the_other_switch_and_a_refused_start():
    async def go():
        h, control = await charging()
        h.transport.bank[R.CHARGING_RELEASE.address] = 1
        _rate_limited(h)
        await control.async_set_lock_evse(True)
        await control.async_set_charging_release(True)
        assert control.pending_action == "lock"
        assert ("replaced", "lock") not in events(control)
        await control.async_shutdown()

    asyncio.run(go())


def test_a_waiting_pause_is_not_taken_by_a_lock_that_must_wait_too():
    import pytest

    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )

    async def go():
        h, control = await charging()
        _rate_limited(h)
        await control.async_pause_charging()
        with pytest.raises(AmtronRateLimitedError):
            await control.async_set_lock_evse(True)
        assert control.pending_action == "pause"
        await control.async_shutdown()

    asyncio.run(go())


def test_a_phase_change_waits_behind_a_waiting_stop():
    import pytest

    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )
    from custom_components.mennekes_amtron_rtu.device.enums import RequestedPhases

    async def go():
        h, control = await charging()
        _rate_limited(h)
        await control.async_set_lock_evse(True)
        h.clock[0] += 301
        current = h.transport.bank.get(R.REQUESTED_PHASES.address)
        other = next(phase for phase in RequestedPhases if int(phase) != current)
        with pytest.raises(AmtronRateLimitedError):
            await control.async_set_requested_phases(other)
        assert control.pending_action == "lock"
        assert ("replaced", "lock") not in events(control)
        await control.async_shutdown()

    asyncio.run(go())


def test_a_planners_waiting_pause_outlasts_a_manual_current_with_a_goal_controller():
    from unittest.mock import AsyncMock

    from custom_components.mennekes_amtron_rtu.device.energy_goal_controller import (
        EnergyGoalController,
    )

    async def go():
        h, control = await charging()
        EnergyGoalController(control, h.client, AsyncMock())
        control.soc_goal.status = "active"
        control.soc_goal.target_percent = 80
        _rate_limited(h)
        deadline = datetime.now(UTC) + timedelta(hours=1)
        await control.async_set_timed_current(None, deadline=deadline, owner="departure")
        h.clock[0] += 10
        await control.async_set_charging_current(10)
        assert currents(h) == [10.0]
        assert control.pending_action == "pause"
        # A manual resume is still the override.
        await control.async_resume_charging()
        assert control.pending_action != "pause"
        await control.async_shutdown()

    asyncio.run(go())


def test_a_guest_start_behind_a_waiting_lock_writes_nothing(tmp_path):
    import pytest

    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )
    from tests import service_guest_fixture as runtime

    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            control = service.control
            clock[0] += 1000
            await control.async_pause_charging()
            await service.refresh()
            await control.async_set_charging_release(False)
            await service.refresh()
            clock[0] += 5
            await control.async_set_lock_evse(True)
            assert control.pending_action == "lock"
            await service.refresh()
            transport.writes.clear()
            with pytest.raises(AmtronRateLimitedError):
                await service.command({
                    "origin": "app", "command": "guest_start",
                    "expected_serial_number": service.identity.serial_number,
                    "expected_guest_revision": service.guest.revision,
                })
            # Refused before the release: nothing written, no guest left behind.
            assert transport.writes == []
            assert control.pending_action == "lock"
            assert service.guest.scope is None
            assert service.client.write_state.guest_permission is None

    asyncio.run(run())
