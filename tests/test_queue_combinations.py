"""Pairs of commands around a waiting stop keep what each of them promised."""

from __future__ import annotations

import asyncio
import struct
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronRateLimitedError,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from tests import service_guest_fixture as runtime
from tests.test_control import Harness, R


async def charging():
    harness = Harness(
        state_change_interval_seconds=300,
        values={"charging_current_ems": 16.0, "evse_state": EvseState.CHARGING},
    )
    control = await harness.connect()
    bank = harness.transport.bank
    bank[R.EVSE_STATE.address] = int(EvseState.CHARGING)
    bank[R.CP_STATE.address] = int(CpState.C2)
    bank[R.CHARGING_RELEASE.address] = 1
    bank[R.LOCK_EVSE.address] = 0
    return harness, control


def currents(harness):
    return [
        round(struct.unpack(">f", struct.pack(">HH", *words))[0], 2)
        for words in harness.written(R.CHARGING_CURRENT_EMS)
    ]


async def drain(control):
    control._pending_changed.set()
    for _ in range(300):
        await asyncio.sleep(0.002)
        if control._flush_task is None or control._flush_task.done():
            break


def test_a_current_behind_a_waiting_lock_is_refused_not_dropped():
    async def run():
        harness, control = await charging()
        await control.async_set_charging_current(11)
        harness.client.write_state.mode_change_at = harness.clock[0]
        harness.clock[0] += 1
        await control.async_set_lock_evse(True)
        assert control.pending_action == "lock"
        harness.clock[0] += 1
        # Nothing would ever write it after the lock: no silent success.
        with pytest.raises(AmtronRateLimitedError):
            await control.async_set_charging_current(6)
        assert (currents(harness), control.pending_action, control.charging_setpoint) == ([11.0], "lock", 11)
        await control.async_shutdown()

    asyncio.run(run())


def test_a_current_behind_a_waiting_pause_is_remembered_for_the_resume():
    async def run():
        harness, control = await charging()
        await control.async_set_charging_current(11)
        harness.client.write_state.mode_change_at = harness.clock[0]
        harness.clock[0] += 1
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        harness.clock[0] += 1
        await control.async_set_charging_current(8)
        assert (currents(harness), control.pending_action, control.charging_setpoint) == ([11.0], "pause", 8)
        await control.async_shutdown()

    asyncio.run(run())


def test_the_users_pause_behind_the_plans_waiting_pause_is_refused_aloud():
    async def run():
        harness, control = await charging()
        control.soc_goal.status, control.soc_goal.target_percent = "active", 80
        harness.client.write_state.mode_change_at = harness.clock[0]
        await control.async_set_departure_current(
            None, deadline=datetime.now(UTC) + timedelta(hours=1), owner="departure",
        )
        harness.clock[0] += 10
        # The plan's pause keeps its place and its owner; the user is told.
        with pytest.raises(AmtronRateLimitedError):
            await control.async_pause_charging()
        assert (control.pending_action, control.pending_owner) == ("pause", "departure")
        await control.async_shutdown()

    asyncio.run(run())


def test_a_latched_stop_leaves_a_waiting_pause_in_place():
    async def run():
        harness, control = await charging()
        await control.async_set_charging_current(11)
        harness.client.write_state.mode_change_at = harness.clock[0]
        harness.clock[0] += 1
        await control.async_pause_charging()
        stop = [True]
        control.time_limit_reached = lambda: stop[0]
        with pytest.raises(AmtronRateLimitedError):
            await control.async_pause_for_energy_goal()
        assert control.pending_action == "pause"
        # The latch is lifted before its own pause was written.
        stop[0] = False
        await control.async_pause_for_energy_goal()
        harness.clock[0] += 301
        await drain(control)
        assert len(currents(harness)) == 2
        assert 0 < currents(harness)[1] < 6
        await control.async_shutdown()

    asyncio.run(run())


def test_a_withdrawn_manual_release_waits_for_its_own_write():
    async def run():
        harness = Harness(
            state_change_interval_seconds=300,
            values={"charging_current_ems": 16.0, "evse_state": EvseState.EV_CONNECTED},
        )
        control = await harness.connect()
        bank = harness.transport.bank
        bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        bank[R.CP_STATE.address] = int(CpState.B1)
        bank[R.CHARGING_RELEASE.address] = 0
        bank[R.LOCK_EVSE.address] = 0
        await control.async_configure_plug_charge(True, policy="c" * 64)

        def data():
            return WallboxData(values={
                "evse_state": bank[R.EVSE_STATE.address], "cp_state": bank[R.CP_STATE.address],
                "charging_release": bank[R.CHARGING_RELEASE.address], "charging_current_ems": 16.0,
            })

        control.observe_plug(data())
        harness.clock[0] += 1000
        await control.async_set_charging_release(True)
        bank[R.EVSE_STATE.address] = int(EvseState.CHARGING)
        bank[R.CP_STATE.address] = int(CpState.C2)
        harness.clock[0] += 10
        before = len(harness.written(R.CHARGING_RELEASE))
        waited = asyncio.Event()

        async def sleep(_delay: float) -> None:
            await waited.wait()

        control._sleep = sleep
        await control.async_set_charging_release(False)
        assert control.pending_action == "disable_charging"
        control.observe_plug(data())
        await asyncio.sleep(0.05)
        # The gate does not close inside the interval what the queue makes wait.
        assert harness.written(R.CHARGING_RELEASE)[before:] == []
        assert control.plug_charge["manual"] is True
        harness.clock[0] += 301
        waited.set()
        await drain(control)
        assert harness.written(R.CHARGING_RELEASE)[before:] == [[0]]
        assert control.plug_charge["manual"] is False
        # A refresh that fails after the acknowledged write changes nothing.
        harness.clock[0] += 1000
        await control.async_set_charging_release(True)
        harness.clock[0] += 1000

        async def broken() -> None:
            raise OSError("refresh failed")

        control._request_refresh = broken
        with pytest.raises(OSError, match="refresh failed"):
            await control.async_set_charging_release(False)
        assert control.plug_charge["manual"] is False
        await control.async_shutdown()

    asyncio.run(run())


def test_a_guest_start_for_a_car_that_just_left_is_refused(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            clock[0] += 1000
            transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
            transport.bank[R.CP_STATE.address] = int(CpState.A1)
            transport.writes.clear()
            with pytest.raises(DeviceError, match="guest_start_not_permitted"):
                await service.command({
                    "origin": "app", "command": "guest_start",
                    "expected_serial_number": service.identity.serial_number,
                    "expected_guest_revision": service.guest.revision,
                })
            assert transport.writes == []
            assert service.guest.scope is None
            assert service.client.write_state.guest_permission is None

    asyncio.run(run())


def test_a_refused_guest_start_keeps_a_release_switched_off_by_hand(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            control = service.control
            clock[0] += 1000
            await control.async_pause_charging()
            await service.refresh()
            clock[0] += 1000
            await control.async_set_charging_release(False)
            await service.refresh()
            assert control.charging_release_requested is False
            revision = service.guest.revision
            transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
            transport.bank[R.CP_STATE.address] = int(CpState.A1)
            transport.writes.clear()
            with pytest.raises(DeviceError):
                await service.command({
                    "origin": "app", "command": "guest_start",
                    "expected_serial_number": service.identity.serial_number,
                    "expected_guest_revision": revision,
                })
            assert transport.writes == []
            assert control.charging_release_requested is False

    asyncio.run(run())


@pytest.mark.parametrize("lifted", ["after_recovery", "during_outage"])
@pytest.mark.parametrize("move", ["later", "clear"])
def test_a_lifted_guest_deadline_does_not_resume_an_outage_pause(tmp_path, move, lifted):
    async def tasks(service):
        if service.goal._task is not None:
            await asyncio.wait_for(service.goal._task, 2)

    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            control = service.control
            await control.goal_controller.async_set_cost_target(10, 0.30, currency="EUR")
            await service.departure.set_trip(datetime.now(UTC) + timedelta(hours=1), "manual")
            await service.command({
                "command": "guest_start", "expected_serial_number": service.identity.serial_number,
                "expected_guest_revision": service.guest.revision,
            })
            clock[0] += 1000
            await service.refresh()
            await tasks(service)
            assert service.guest.active
            # The meter drops out: the goal pauses and may resume on recovery.
            generation = service.client.write_state.energy_goal_generation
            service.goal.observe(WallboxData(energy_read=True, energy_goal_generation=generation))
            await tasks(service)
            assert control.energy_goal.resume_on_recovery is True
            written = len(runtime.current_writes(transport))
            # The guest deadline passes during the outage, then the meter is back.
            await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
            clock[0] += 1000
            await service.guest.tick()
            # The stop by the deadline ended the goal's right to resume.
            assert control.energy_goal.resume_on_recovery is False
            later = None if move == "clear" else datetime.now(UTC) + timedelta(hours=2)
            if lifted == "during_outage":
                await service.departure.set_trip(later, "manual")
            service._poller._slow_read_at = None
            await service.refresh()
            await tasks(service)
            assert control.energy_goal.status == "active"
            # Moving the time later lifts the limit; it must not start a charge.
            await service.departure.set_trip(later, "manual")
            clock[0] += 1000
            service._poller._slow_read_at = None
            await service.refresh()
            await tasks(service)
            await service.guest.tick()
            assert len(runtime.current_writes(transport)) == written
            assert control.pause_requested is True

    asyncio.run(run())


def test_a_guest_start_whose_release_was_written_keeps_its_guards(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            clock[0] += 1000
            transport.bank[R.CHARGING_RELEASE.address] = 0
            await service.refresh()
            transport.writes.clear()
            original = transport._write

            def write(address, values, function):
                result = original(address, values, function)
                if address == R.CHARGING_RELEASE.address:
                    # The car leaves between the release and the current.
                    transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
                    transport.bank[R.CP_STATE.address] = int(CpState.A1)
                return result

            transport._write = write
            # It returns instead of raising a clean refusal after a write.
            await service.command({
                "origin": "app", "command": "guest_start",
                "expected_serial_number": service.identity.serial_number,
                "expected_guest_revision": service.guest.revision,
            })
            assert [address for address, _values in transport.writes] == [R.CHARGING_RELEASE.address]

    asyncio.run(run())
