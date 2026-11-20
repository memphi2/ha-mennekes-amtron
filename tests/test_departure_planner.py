"""The real service plans a handed-over departure through its guarded path."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import (
    departure_planner as departure_runtime,
)
from custom_components.mennekes_amtron_rtu.device import server
from custom_components.mennekes_amtron_rtu.device.connection_sample import (
    connection_sample,
)
from custom_components.mennekes_amtron_rtu.device.departure_plan import (
    DepartureInputs,
)
from custom_components.mennekes_amtron_rtu.device.departure_store import (
    DepartureStore,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.operation import Operation
from custom_components.mennekes_amtron_rtu.device.service_runtime import (
    ModbusService,
)
from tests import service_guest_fixture as runtime

CAR = "profile:bmw:sensor.soc|"


@pytest.fixture
def departure_service(tmp_path, monkeypatch):
    return lambda: runtime.guest_service(tmp_path)


async def hand_over(service, *, hours=2.0, soc=50.0):
    """Do what Home Assistant does: select the car, set the trip, confirm it."""
    service.control.soc_goal.configure_source(CAR)
    service.control.soc_goal.set_target(80)
    await service.refresh()
    serial = service.identity.serial_number
    scope = service.snapshot()["control"]["departure"]["scope"]
    deadline = datetime.now(UTC) + timedelta(hours=hours)
    await service.command({
        "command": "departure_trip", "deadline": deadline.isoformat(), "source": "manual",
        "expected_serial_number": serial,
    })
    given = DepartureInputs(
        CAR, scope, 60.0, None, soc, datetime.now(UTC) - timedelta(minutes=1),
    )
    result = await service.command({
        "command": "departure_inputs", "value": given.snapshot(), "expected_serial_number": serial,
    })
    return given, result


def test_the_service_charges_and_rests_a_handed_over_departure(departure_service):
    async def run():
        async with departure_service() as (s, transport, clock):
            assert server.ModbusService is ModbusService
            given, result = await hand_over(s)
            departure = result["control"]["departure"]
            assert departure["inputs"] == given.digest
            assert departure["trip"]["source"] == "manual"
            await s.departure.tick()
            assert (s.departure.status, s.departure.confirmed) == ("charging", True)
            assert runtime.current_writes(transport)[-1] == 16
            assert s.snapshot()["control"]["departure"]["estimated_soc"] == 50.0
            # The car leaves: the plan withdraws its own queued command.
            runtime.put(transport.bank, runtime.R.EVSE_STATE, 1)
            runtime.put(transport.bank, runtime.R.CP_STATE, 10)
            clock[0] += 10
            await s.refresh()
            await s.departure.tick()
            assert s.departure.status == "vehicle_unconfirmed"
            assert s.departure.anchor is None
    asyncio.run(run())


def test_a_late_start_pauses_through_the_control_and_survives_a_restart(departure_service, tmp_path):
    async def run():
        async with departure_service() as (s, transport, _clock):
            await hand_over(s, hours=10)
            await s.departure.tick()
            assert s.departure.status == "paused_until_start"
            assert s.control.pause_requested is True
            assert s.departure.plan_paused
            assert runtime.current_writes(transport)[-1] == 1
        saved = DepartureStore(tmp_path / "departure-plan.json", s.departure.store.identity).load()
        assert saved["plan_paused"] is True
        assert saved["inputs"]["vehicle"] == CAR
        async with departure_service() as (restarted, _transport, _clock):
            assert restarted.departure.plan_paused
            assert restarted.departure.trip.deadline is not None
    asyncio.run(run())


def test_the_snapshot_summarizes_who_controls_the_charge(departure_service):
    async def run():
        async with departure_service() as (s, _transport, _clock):
            await s.refresh()
            summary = s.snapshot()["control"]["operation"]
            assert (summary["controller"], summary["waiting"], summary["vehicle"]) == (
                "direct", "none", "connected",
            )
            await hand_over(s, hours=10)
            await s.departure.tick()
            summary = s.snapshot()["control"]["operation"]
            assert summary["controller"] == "departure_plan"
            assert summary["waiting"] == "until_planned_start"
            assert summary["vehicle"] == "confirmed"
            assert summary["next"] == {
                "event": "plan_start", "at": s.departure.planned_start.isoformat(),
            }
            assert summary["target"] == {
                "kind": "level", "value": 80.0, "deadline": s.departure.trip.deadline.isoformat(),
            }
            assert Operation.restore(summary).controller == "departure_plan"
            s.guest.scope = connection_sample(s).scope
            assert s.snapshot()["control"]["operation"]["controller"] == "guest"

    asyncio.run(run())


def test_departure_commands_are_validated_before_the_plan_sees_them(departure_service):
    async def run():
        async with departure_service() as (s, _transport, _clock):
            serial = s.identity.serial_number
            trip = {"command": "departure_trip", "deadline": None, "source": None, "expected_serial_number": serial}
            for payload, error in (
                ({**trip, "value": 1}, ValueError),
                ({key: value for key, value in trip.items() if key != "expected_serial_number"}, ValueError),
                ({**trip, "deadline": 5}, ValueError),
                ({**trip, "source": 5}, ValueError),
                ({**trip, "withdraw": "yes"}, ValueError),
                ({**trip, "deadline": "2026-11-13T22:00:00"}, ValueError),
                ({**trip, "source": ""}, ValueError),
                ({"command": "departure_inputs", "value": {"vehicle": CAR}, "expected_serial_number": serial}, ValueError),
                ({**trip, "expected_serial_number": "other"}, DeviceError),
            ):
                with pytest.raises(error):
                    await s.command(payload)
            result = await s.command({**trip, "withdraw": True})
            assert result["control"]["departure"]["status"] == "disabled"
            # Ordinary commands still reach the base service.
            with pytest.raises(ValueError, match="unknown command"):
                await s.command({"command": "nonsense", "expected_serial_number": serial})
            plan, s.planner.controller = s.departure, None
            with pytest.raises(DeviceError, match="service_unavailable"):
                await s.command(trip)
            assert s.snapshot()["control"]["departure"] is None
            s.planner.controller = plan
    asyncio.run(run())


def test_plan_writes_need_an_available_wallbox(departure_service):
    async def run():
        async with departure_service() as (s, _transport, _clock):
            s._success = False
            deadline = datetime.now(UTC) + timedelta(hours=1)
            with pytest.raises(DeviceError, match="service_unavailable"):
                await s.planner._apply_departure(16.0, deadline)
            with pytest.raises(DeviceError, match="service_unavailable"):
                await s.planner._end_departure_pause()
            control, s.control = s.control, None
            await s.planner._cancel_departure()
            assert s.planner._departure_sample().goal_status == "disabled"
            s.control = control
            # Cancelling is queue bookkeeping and works without the bus.
            await s.planner._cancel_departure()
            assert s.control.pending_action is None
    asyncio.run(run())


def test_a_plan_pause_ends_through_a_resume_with_the_service_cap(departure_service):
    async def run():
        async with departure_service() as (s, transport, clock):
            await hand_over(s, hours=10)
            await s.departure.tick()
            assert s.control.pause_requested is True
            clock[0] += 400
            await s.planner._end_departure_pause()
            # The resume restores the current from before the plan's pause.
            assert s.control.pause_requested is False
            assert runtime.current_writes(transport)[-1] == 8
            assert s.control.pending_action is None
    asyncio.run(run())


def test_the_loop_survives_a_failing_tick_and_wakes_on_commands(
    departure_service, monkeypatch, caplog,
):
    async def run():
        caplog.set_level("DEBUG")
        async with departure_service() as (s, _transport, _clock):
            calls = []

            async def tick():
                calls.append(len(calls))
                if len(calls) <= 3:
                    s.departure.wake.set()
                    raise RuntimeError("fixture")
                raise asyncio.CancelledError

            s.departure.tick = tick
            with pytest.raises(asyncio.CancelledError):
                await s.planner.loop()
            assert calls == [0, 1, 2, 3]
            assert s.departure.status == "error"
            # A tick that fails every five seconds is reported once.
            reports = [r for r in caplog.records if "tick failed" in r.getMessage()]
            assert [(r.levelname, bool(r.exc_info)) for r in reports] == [
                ("ERROR", True), ("DEBUG", False), ("DEBUG", False),
            ]
            assert reports[0].getMessage() == "Departure plan: tick failed (RuntimeError)"
            plan, s.planner.controller = s.departure, None

            async def stop(_delay):
                raise asyncio.CancelledError

            monkeypatch.setattr(departure_runtime.asyncio, "sleep", stop)
            with pytest.raises(asyncio.CancelledError):
                await s.planner.loop()
            monkeypatch.undo()
            s.planner.controller = plan
    asyncio.run(run())


def test_the_jobs_loop_runs_the_plan_beside_the_orders(departure_service, monkeypatch):
    class StopLoopError(Exception):
        pass

    async def run():
        async with departure_service() as (s, _transport, _clock):
            started = asyncio.Event()

            async def departure_loop():
                await started.wait()
                raise StopLoopError

            async def orders():
                started.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(s.planner, "loop", departure_loop)
            monkeypatch.setattr(s.guest, "loop", orders)
            monkeypatch.setattr(s, "_run_bus", orders)
            # The part that failed ends the service with its own error.
            with pytest.raises(StopLoopError):
                await s._run()
    asyncio.run(run())


def test_initialisation_needs_a_configured_plan(departure_service):
    async def run():
        async with departure_service() as (s, _transport, _clock):
            plan, s.planner.controller = s.departure, None
            with pytest.raises(RuntimeError, match="not configured"):
                await s.planner.initialize()
            s.planner.controller = plan
    asyncio.run(run())


@pytest.mark.parametrize("action", ["resume", "pause", "current"])
def test_guest_start_invalidates_departure_writes_already_waiting_for_command_lock(departure_service, action):
    """The earlier plan decision must not run after a guest takes control."""
    async def run():
        async with departure_service() as (service, transport, clock):
            await hand_over(service)
            await service.departure.tick()
            clock[0] += 1000
            await service.control.async_pause_charging()
            clock[0] += 1000
            await service.command_lock.acquire()
            guest = asyncio.create_task(service.command({
                "command": "guest_start",
                "expected_serial_number": service.identity.serial_number,
                "expected_guest_revision": service.guest.revision,
            }))
            await asyncio.sleep(0)
            deadline = datetime.now(UTC) + timedelta(hours=1)
            old_plan = asyncio.create_task(
                service.planner._end_departure_pause() if action == "resume"
                else service.planner._apply_departure(None if action == "pause" else 10, deadline)
            )
            await asyncio.sleep(0)
            assert not guest.done()
            assert not old_plan.done()
            service.command_lock.release()
            await guest
            with pytest.raises(DeviceError, match="soc_goal_source_changed"):
                await old_plan
            assert service.guest.scope is not None
            assert service.control.pause_requested is False
            assert service.control.pending_action is None
            assert runtime.current_writes(transport)[-1] >= 6
    asyncio.run(run())


def test_departure_resume_cannot_override_manual_guest_pause(departure_service):
    async def run():
        async with departure_service() as (service, transport, clock):
            await hand_over(service)
            await service.departure.tick()
            clock[0] += 1000
            await service.command({
                "command": "guest_start",
                "expected_serial_number": service.identity.serial_number,
                "expected_guest_revision": service.guest.revision,
            })
            clock[0] += 1000
            await service.command({
                "command": "pause", "expected_serial_number": service.identity.serial_number,
            })
            before = list(transport.writes)
            clock[0] += 1000
            with pytest.raises(DeviceError, match="soc_goal_source_changed"):
                await service.planner._end_departure_pause()
            assert service.control.pause_requested is True
            assert service.control.pending_action is None
            assert transport.writes == before
    asyncio.run(run())
