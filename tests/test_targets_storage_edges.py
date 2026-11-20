"""Targets and storage: ownership of a pause, cancellation, clock steps, a full disk."""

import asyncio
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests import service_guest_fixture as runtime
from tests.test_departure_plan import NOW, Box, inputs
from tests.test_departure_planner import hand_over


@pytest.mark.parametrize("looked", [True, False])
def test_the_plan_does_not_end_a_guests_pause_after_the_unplug(tmp_path, looked):
    async def run():
        async with runtime.guest_service(tmp_path) as (s, transport, clock):
            serial = s.identity.serial_number
            await hand_over(s, hours=10)
            await s.departure.tick()
            assert s.departure.status == "paused_until_start"
            assert s.departure.plan_paused
            assert s.control.pause_requested is True
            clock[0] += 1000
            await s.command(
                {
                    "command": "guest_start",
                    "expected_serial_number": serial,
                    "expected_guest_revision": s.guest.revision,
                }
            )
            assert s.control.pause_requested is False
            if looked:
                await s.departure.tick()
            # the guest is paused by hand, also before the plan looked again
            clock[0] += 1000
            await s.command({"command": "pause", "expected_serial_number": serial})
            assert s.control.pause_requested is True
            await s.departure.tick()
            # the guest leaves
            runtime.put(transport.bank, runtime.R.EVSE_STATE, 1)
            runtime.put(transport.bank, runtime.R.CP_STATE, 10)
            clock[0] += 1000
            await s.refresh()
            assert not s.guest.mode
            list(runtime.current_writes(transport))
            await s.departure.tick()
            assert s.control.pause_requested is True, (
                "manual pause of the guest was lifted by the plan"
            )

    asyncio.run(run())


def test_a_cancelled_tick_stays_cancelled_when_its_save_fails(tmp_path):
    async def go():
        box = Box(tmp_path)
        await box.plan.initialize()
        gate, entered = threading.Event(), threading.Event()

        def save(_plan):
            entered.set()
            gate.wait(5)
            raise OSError("disk full")

        await box.plan.set_inputs(inputs())
        box.plan.store.save = save
        box.plan.trip.set(NOW + timedelta(hours=2), "manual")
        task = asyncio.create_task(box.plan.tick())
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.01)
        gate.set()
        _done, _ = await asyncio.wait({task}, timeout=5)
        assert task.cancelled()

    asyncio.run(go())


def test_a_cancelled_intent_save_stays_cancelled_when_it_fails(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (s, _transport, _clock):
            gate, entered = threading.Event(), threading.Event()

            def save(_state):
                entered.set()
                gate.wait(5)
                raise OSError("disk full")

            s._store.save = save
            reached = []

            async def poll_step():
                await s._save_intent()
                reached.append("continued after save")
                await asyncio.sleep(3600)

            task = asyncio.create_task(poll_step())
            await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.01)
            gate.set()
            _done, _pending = await asyncio.wait({task}, timeout=2)
            s._store.save = lambda _state: None
            hung = not task.done()
            task.cancel()
            assert not hung, "the cancelled poll went on after the failed save"

    asyncio.run(run())


def test_a_clock_set_back_does_not_anchor_a_report_to_an_old_reading(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(
            hours=6, given=inputs(capacity_kwh=60.0)
        )  # 50 % at meter 100.0, 22:00
        box.set(target_percent=90.0)
        # the car charges 12 kWh in an hour (22:05 .. 23:00)
        for step in range(1, 13):
            at = NOW + timedelta(minutes=5 * step)
            box.set(
                now=at,
                meter_at=at.timestamp(),
                total_kwh=100.0 + step,
                paused=False,
                applied_a=16.0,
            )
            plan.observe()
        box.clock[0] += 4000
        await plan.tick()
        # The wall clock is set back by 55 minutes (23:00 -> 22:05); charging goes on.
        for minute, total in ((7, 112.5), (9, 113.0), (11, 113.5)):
            at = NOW + timedelta(minutes=minute)
            box.set(now=at, meter_at=at.timestamp(), total_kwh=total)
            plan.observe()
        # The car reports its real level, 71 %, at 22:10 of the corrected clock.
        await plan.set_inputs(
            inputs(soc_percent=71.0, soc_observed_at=NOW + timedelta(minutes=10))
        )
        box.clock[0] += 4000
        await plan.tick()
        assert plan.anchor.meter_kwh >= 113.0, plan.anchor
        assert plan.status != "estimated_reached"

    asyncio.run(go())


@pytest.mark.parametrize("guest", [False, True])
def test_the_service_starts_with_a_disk_that_refuses_the_save(
    tmp_path, monkeypatch, guest
):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            await service.departure.set_trip(
                datetime.now(UTC) + timedelta(hours=1), "manual"
            )
            clock[0] += 1000
            if guest:
                await service.command(
                    {
                        "command": "guest_start",
                        "expected_serial_number": service.identity.serial_number,
                        "expected_guest_revision": service.guest.revision,
                    }
                )
            else:
                await service.command(
                    {
                        "command": "set_current",
                        "value": 10,
                        "expected_serial_number": service.identity.serial_number,
                    }
                )
            bank = transport.bank
        # While the service is down the car leaves; the disk is full at the restart.
        runtime.put(bank, runtime.R.EVSE_STATE, 1)
        runtime.put(bank, runtime.R.CP_STATE, 10)

        def full(self, state):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(IntentStore, "save", full)
        async with runtime.guest_service(tmp_path, bank=bank) as (
            service,
            _transport,
            _clock,
        ):
            # It runs and controls; only the save is reported as failed.
            assert service.control is not None

    asyncio.run(run())


def test_the_plans_current_does_not_replace_the_current_chosen_by_hand(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            serial = service.identity.serial_number
            chosen = service.control.charging_setpoint
            await hand_over(service, hours=2)
            await service.departure.tick()
            planned = runtime.current_writes(transport)[-1]
            assert planned >= 6
            assert planned != chosen
            assert service.control.charging_setpoint == chosen
            await service.command(
                {
                    "command": "departure_trip",
                    "deadline": None,
                    "source": None,
                    "withdraw": True,
                    "expected_serial_number": serial,
                }
            )
            clock[0] += 1000
            await service.departure.tick()
            await service.refresh()
            await service.command(
                {"command": "pause", "expected_serial_number": serial}
            )
            clock[0] += 1000
            await service.command(
                {"command": "resume", "expected_serial_number": serial}
            )
            # Back to the chosen current, not the plan's.
            assert runtime.current_writes(transport)[-1] == chosen

    asyncio.run(run())


def test_the_plan_ends_its_pause_when_a_queued_guest_start_never_ran(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (s, transport, clock):
            serial = s.identity.serial_number
            await hand_over(s, hours=10)
            await s.departure.tick()
            assert s.departure.status == "paused_until_start"
            assert s.departure.plan_paused
            assert s.control.pause_requested is True
            # guest start inside the state-change interval of the plan's pause
            clock[0] += 5
            with suppress(DeviceError):
                await s.command(
                    {
                        "command": "guest_start",
                        "expected_serial_number": serial,
                        "expected_guest_revision": s.guest.revision,
                    }
                )
            assert s.guest.mode
            assert s.control.pending_action == "resume"
            assert (
                s.control.pause_requested is True
            )  # the plan's pause is still what the wallbox has
            await s.departure.tick()
            # the guest leaves before the queued start ran
            runtime.put(transport.bank, runtime.R.EVSE_STATE, 1)
            runtime.put(transport.bank, runtime.R.CP_STATE, 10)
            clock[0] += 1
            await s.refresh()
            for _ in range(20):
                s.control._pending_changed.set()
                await asyncio.sleep(0.01)
            assert s.control.pending_action is None
            assert not s.guest.mode
            clock[0] += 1000
            await s.departure.tick()
            await s.refresh()
            await s.departure.tick()
            assert s.control.pause_requested is not True, (
                "the plan's own pause was left on the wallbox"
            )

    asyncio.run(run())


def test_an_intent_file_far_too_large_counts_as_damaged(tmp_path):
    store = IntentStore(tmp_path / "intent.json", "serial")
    store.path.write_bytes(b" " * 70000)
    assert store.load(WriteState()) is False


def test_a_current_written_before_the_plans_waiting_pause_ends_no_pause(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (s, transport, clock):
            serial = s.identity.serial_number
            # A new installation: whether a pause is on is not known yet.
            assert s.control.pause_requested is None
            await hand_over(s, hours=10)
            # A switch a moment ago: the plan's pause has to wait.
            s.control._write_state.mode_change_at = clock[0]
            await s.departure.tick()
            assert s.departure.plan_paused
            clock[0] += 6
            await s.command({"command": "set_current", "value": 10, "expected_serial_number": serial})
            assert s.control.writes.pause_ends == 0
            clock[0] += 1000
            for _ in range(30):
                s.control._pending_changed.set()
                await asyncio.sleep(0.01)
            await s.refresh()
            await s.departure.tick()
            assert s.departure.plan_paused
            assert s.control.pause_requested is True
            clock[0] += 5
            with suppress(DeviceError):
                await s.command(
                    {
                        "command": "guest_start",
                        "expected_serial_number": serial,
                        "expected_guest_revision": s.guest.revision,
                    }
                )
            assert s.guest.mode
            assert s.control.pending_action == "resume"
            await s.departure.tick()
            # The guest's start still waits: the pause stays the plan's.
            assert s.departure.plan_paused
            runtime.put(transport.bank, runtime.R.EVSE_STATE, 1)
            runtime.put(transport.bank, runtime.R.CP_STATE, 10)
            clock[0] += 1
            await s.refresh()
            for _ in range(20):
                s.control._pending_changed.set()
                await asyncio.sleep(0.01)
            assert not s.guest.mode
            clock[0] += 1000
            await s.departure.tick()
            await s.refresh()
            await s.departure.tick()
            assert s.control.pause_requested is not True

    asyncio.run(run())
