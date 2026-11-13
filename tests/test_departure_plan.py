"""The service's departure plan: binding, projection and the provisional stop."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import departure_plan as dp
from custom_components.mennekes_amtron_rtu.device.charge_job import SessionMark
from custom_components.mennekes_amtron_rtu.device.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.device.departure_plan import (
    DepartureController,
    DepartureInputs,
    DepartureSample,
    SocAnchor,
    instant,
)
from custom_components.mennekes_amtron_rtu.device.departure_store import (
    DepartureStore,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError

NOW = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)
SCOPE = "a" * 64
OTHER = "b" * 64
CAR = "profile:bmw:sensor.soc|"
MARK = SessionMark(NOW, 3600, 5.0, 7, "c" * 64)


def sample(**changes) -> DepartureSample:
    return replace(DepartureSample(
        now=NOW, available=True, present=True, scope=SCOPE, session=MARK,
        total_kwh=100.0, meter_at=NOW.timestamp(), applied_a=8.0, max_current_a=16.0,
        paused=False, pause_requested=False, pending=False, read_only=False,
        vehicle=CAR, target_percent=80.0, goal_status="active", target_is_limit=False,
        energy_reached=False, goal_stop=False, order_active=False, phases=3,
    ), **changes)


def inputs(**changes) -> DepartureInputs:
    values = {
        "vehicle": CAR, "scope": SCOPE, "capacity_kwh": 60.0, "phases": None,
        "soc_percent": 50.0, "soc_observed_at": NOW - timedelta(minutes=10),
    }
    return DepartureInputs(**{**values, **changes})


class Box:
    """A wallbox double recording what the plan writes, cancels and resumes."""

    def __init__(self, tmp_path, **changes):
        self.path = tmp_path / "departure-plan.json"
        self.state = sample(**changes)
        self.writes: list[float | None] = []
        self.cancels = 0
        self.resumes = 0
        self.clock = [1000.0]
        self.fail: Exception | None = None
        self.plan = self.controller()

    def controller(self) -> DepartureController:
        return DepartureController(
            DepartureStore(self.path, "fixture"), lambda: self.state,
            self.apply, self.cancel, self.resume, monotonic=lambda: self.clock[0],
        )

    async def apply(self, value, _deadline):
        if self.fail is not None:
            raise self.fail
        self.writes.append(value)
        self.set(paused=value is None, pause_requested=value is None,
                 applied_a=self.state.applied_a if value is None else value)

    async def cancel(self):
        self.cancels += 1

    async def resume(self):
        if self.fail is not None:
            raise self.fail
        self.resumes += 1
        self.set(paused=False, pause_requested=False)

    def set(self, **changes):
        self.state = replace(self.state, **changes)

    async def start(self, *, hours: float = 2, given: DepartureInputs | None = None):
        await self.plan.initialize()
        await self.plan.set_trip(NOW + timedelta(hours=hours), "manual")
        await self.plan.set_inputs(given or inputs())
        await self.plan.tick()
        return self.plan


def run(coro):
    return asyncio.run(coro)


def test_inputs_round_trip_and_digest_changes_with_any_field():
    given = inputs(phases=1, soc_observed_at=datetime(2026, 11, 13, 23, 0, tzinfo=UTC).astimezone())
    restored = DepartureInputs.restore(given.snapshot())
    assert restored == given
    assert restored.digest == given.digest
    assert restored.soc_observed_at.tzinfo is UTC
    assert inputs(capacity_kwh=61).digest != given.digest
    assert DepartureInputs(CAR).snapshot() == {
        "vehicle": CAR, "scope": None, "capacity_kwh": None, "phases": None,
        "soc_percent": None, "soc_observed_at": None,
    }


@pytest.mark.parametrize(("change", "message"), [
    ({"vehicle": ""}, "vehicle"), ({"vehicle": "x" * 513}, "vehicle"), ({"vehicle": 3}, "vehicle"),
    ({"scope": "A" * 64}, "scope"), ({"scope": "a"}, "scope"),
    ({"capacity_kwh": 0}, "capacity"), ({"capacity_kwh": True}, "capacity"),
    ({"capacity_kwh": 251}, "capacity"), ({"phases": 4}, "phases"), ({"phases": True}, "phases"),
    ({"soc_percent": None}, "report time"), ({"soc_observed_at": None}, "report time"),
    ({"soc_percent": 101}, "percentage"),
    ({"soc_observed_at": NOW.replace(tzinfo=None)}, "timezone"),
    ({"soc_observed_at": "2026-11-13T22:00:00+00:00"}, "timezone"),
])
def test_inputs_reject_anything_out_of_bounds(change, message):
    with pytest.raises(ValueError, match=message):
        inputs(**change)


@pytest.mark.parametrize(("raw", "message"), [
    (None, "fields"), ({}, "fields"), ({**inputs().snapshot(), "extra": 1}, "fields"),
    ({**inputs().snapshot(), "soc_observed_at": "2026-11-13T22:00:00"}, "timezone"),
    ({**inputs().snapshot(), "soc_observed_at": 5}, "invalid timestamp"),
])
def test_restored_inputs_need_exact_fields(raw, message):
    with pytest.raises(ValueError, match=message):
        DepartureInputs.restore(raw)


@pytest.mark.parametrize(("raw", "message"), [
    (None, "fields"), ({"percent": 50, "observed_at": NOW.isoformat()}, "fields"),
    ({"percent": 50, "observed_at": NOW.isoformat(), "meter_kwh": -1}, "meter"),
    ({"percent": 50, "observed_at": NOW.isoformat(), "meter_kwh": True}, "meter"),
    ({"percent": 50, "observed_at": NOW.isoformat(), "meter_kwh": float("inf")}, "meter"),
    ({"percent": 150, "observed_at": NOW.isoformat(), "meter_kwh": 1}, "percentage"),
])
def test_an_anchor_needs_a_level_time_and_meter(raw, message):
    with pytest.raises(ValueError, match=message):
        SocAnchor.restore(raw)


@pytest.mark.parametrize(("raw", "message"), [
    ("2026-11-13T22:00:00", "timezone"), (1, "invalid timestamp"), ("x" * 65, "invalid timestamp"),
])
def test_instant_needs_an_offset(raw, message):
    assert instant("2026-11-13T23:00:00+01:00") == NOW
    with pytest.raises(ValueError, match=message):
        instant(raw)


def test_without_a_trip_or_car_nothing_is_planned(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = box.plan
        await plan.tick()
        assert plan.status == "disabled"
        await plan.initialize()
        await plan.tick()
        assert (plan.status, plan.snapshot()) == ("disabled", {
            "trip": {"deadline": None, "source": None, "finished": None}, "status": "disabled",
            "planned_start": None, "estimated_soc": None, "inputs": None, "scope": SCOPE,
            "confirmed": False,
        })
        await plan.set_trip(NOW + timedelta(hours=2), "manual")
        await plan.tick()
        assert plan.status == "vehicle_unconfirmed"
        assert box.writes == []
    run(go())


def test_a_late_start_pauses_until_it_then_charges_at_full_current(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        assert plan.status == "paused_until_start"
        start = plan.planned_start
        assert start is not None
        assert start > NOW + timedelta(hours=7)
        assert box.writes == [None]
        assert plan.plan_paused
        assert plan.snapshot()["estimated_soc"] == 50.0
        # The start arrives: the plan charges, after its write interval.
        box.set(now=start + timedelta(seconds=1), meter_at=(start + timedelta(seconds=1)).timestamp())
        box.clock[0] += 300
        await plan.tick()
        assert (plan.status, box.writes) == ("charging", [None, 16.0])
        # The pause is the plan's until the wallbox shows that it ended.
        assert plan.plan_paused
        await plan.tick()
        assert not plan.plan_paused
        # The start that passed stays the plan's start.
        assert plan.planned_start == start
        assert box.writes == [None, 16.0]
    run(go())


def test_a_start_that_never_landed_leaves_the_pause_to_the_plan(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        start = plan.planned_start
        box.set(now=start + timedelta(seconds=1), meter_at=(start + timedelta(seconds=1)).timestamp())
        box.clock[0] += 300
        await plan.tick()
        assert box.writes == [None, 16.0]
        # The start is still queued, or its write was lost: the car stays paused.
        box.set(paused=True, pause_requested=True)
        await plan.tick()
        assert plan.plan_paused
        box.set(present=False)
        await plan.tick()
        assert (box.resumes, plan.plan_paused) == (1, False)
    run(go())


def test_the_same_level_reported_again_keeps_the_metered_energy(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        anchor = plan.anchor
        later = NOW + timedelta(minutes=30)
        box.set(now=later, total_kwh=110.0, meter_at=later.timestamp())
        await plan.set_inputs(inputs(soc_observed_at=later - timedelta(minutes=1)))
        await plan.tick()
        assert plan.anchor == anchor
        # 10 kWh x 0.9 / 60 kWh still count on top of the repeated 50 %.
        assert plan.snapshot()["estimated_soc"] == 65.0
    run(go())


def test_an_unknown_pause_intent_is_asked_again_before_the_pause_ends(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        box.set(present=False, pause_requested=None)
        await plan.tick()
        assert (box.resumes, plan.plan_paused) == (0, True)
        box.set(pause_requested=True)
        await plan.tick()
        assert (box.resumes, plan.plan_paused) == (1, False)
    run(go())


def test_the_projection_stops_at_the_target_and_a_real_reading_decides(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        assert (plan.status, box.writes) == ("charging", [16.0])
        # 20 kWh metered x 0.9 efficiency / 60 kWh is the missing 30 %.
        later = NOW + timedelta(hours=1)
        box.set(now=later, total_kwh=120.0, meter_at=later.timestamp())
        box.clock[0] += 10
        await plan.tick()
        # The stop skips the write interval once after a current of the plan.
        assert (plan.status, box.writes, plan.estimated_stop) == ("estimated_reached", [16.0, None], True)
        assert plan.snapshot()["estimated_soc"] == 80.0
        # A reading just below the target holds the stop.
        box.set(now=later + timedelta(minutes=2), meter_at=(later + timedelta(minutes=2)).timestamp())
        await plan.set_inputs(inputs(soc_percent=79.5, soc_observed_at=later + timedelta(minutes=1)))
        await plan.tick()
        assert (plan.status, box.writes) == ("estimated_reached", [16.0, None])
        # A reading further below plans the rest of the charge again.
        await plan.set_inputs(inputs(soc_percent=78, soc_observed_at=later + timedelta(minutes=2)))
        await plan.tick()
        assert not plan.estimated_stop
        assert plan.status == "paused_until_start"
        assert plan.snapshot()["estimated_soc"] == 78.0
    run(go())


def test_at_its_own_limit_the_car_is_not_paused_on_an_estimate(tmp_path):
    async def go():
        box = Box(tmp_path, target_is_limit=True)
        plan = await box.start()
        later = NOW + timedelta(hours=1)
        box.set(now=later, total_kwh=121.0, meter_at=later.timestamp())
        await plan.tick()
        assert (plan.status, box.writes) == ("estimated_reached", [16.0])
    run(go())


def test_the_deadline_finishes_an_estimated_stop_as_reached_and_others_as_late(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        box.set(now=NOW + timedelta(hours=1), total_kwh=120.0, meter_at=(NOW + timedelta(hours=1)).timestamp())
        await plan.tick()
        box.set(now=NOW + timedelta(hours=3))
        await plan.tick()
        assert plan.trip.snapshot() == {"deadline": None, "source": "manual", "finished": "reached"}
        assert (plan.status, plan.estimated_stop) == ("reached", False)
        await plan.set_trip(NOW + timedelta(hours=4), "manual")
        box.set(now=NOW + timedelta(hours=5))
        await plan.tick()
        assert plan.trip.finished == "late"
        assert plan.status == "late"
    run(go())


def test_a_reported_target_finishes_and_only_a_higher_target_plans_again(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        box.set(goal_status="reached")
        await plan.tick()
        assert plan.trip.finished == "reached"
        assert plan.status == "reached"
        cancels = box.cancels
        box.set(goal_status="active")
        await plan.tick()
        assert plan.status == "reached"
        assert box.cancels == cancels
        box.set(target_percent=90.0)
        box.clock[0] += 300
        await plan.tick()
        assert plan.trip.finished is None
        assert plan.status == "unreachable"
        # An energy goal that holds finishes the trip as well.
        box.set(energy_reached=True)
        await plan.tick()
        assert plan.trip.finished == "reached"
        box.set(energy_reached=False, target_percent=95.0)
        await plan.set_inputs(inputs(soc_percent=None, soc_observed_at=None))
        plan.anchor = None
        await plan.tick()
        # Without a known charge level a raised target cannot be judged.
        assert plan.trip.finished == "reached"
    run(go())


@pytest.mark.parametrize(("changes", "status"), [
    ({"goal_status": "disabled"}, "waiting_for_target"),
    ({"target_percent": 0.0}, "waiting_for_target"),
    ({"available": False}, "waiting_for_device"),
    ({"read_only": True}, "read_only"),
    ({"total_kwh": None, "meter_at": None}, "waiting_for_device"),
    ({"order_active": True}, "order_active"),
    ({"goal_stop": True}, "waiting_for_device"),
])
def test_missing_inputs_wait_without_writing(tmp_path, changes, status):
    async def go():
        box = Box(tmp_path, **changes)
        plan = await box.start()
        assert plan.status == status
        assert box.writes == []
    run(go())


@pytest.mark.parametrize(("given", "status"), [
    (inputs(capacity_kwh=None), "waiting_for_capacity"),
    (inputs(soc_percent=None, soc_observed_at=None), "waiting_for_soc"),
    (inputs(soc_observed_at=NOW + timedelta(minutes=6)), "waiting_for_soc"),
])
def test_a_car_without_capacity_or_reading_waits(tmp_path, given, status):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(given=given)
        assert plan.status == status
        assert box.writes == []
    run(go())


def test_a_slightly_early_report_counts_from_now(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(given=inputs(soc_observed_at=NOW + timedelta(minutes=4)))
        assert plan.anchor == SocAnchor(50.0, NOW, 100.0)
    run(go())


def test_a_started_charge_survives_a_short_gap_in_the_inputs(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        started = plan.planned_start
        box.set(available=False, now=NOW + timedelta(minutes=5))
        await plan.tick()
        assert (plan.status, plan.planned_start) == ("waiting_for_device", started)
    run(go())


def test_a_reading_waits_for_a_newer_meter_reading(tmp_path):
    async def go():
        box = Box(tmp_path)
        await box.plan.initialize()
        await box.plan.set_trip(NOW + timedelta(hours=2), "manual")
        await box.plan.set_inputs(inputs(soc_observed_at=NOW))
        box.set(meter_at=(NOW - timedelta(seconds=10)).timestamp())
        await box.plan.tick()
        # The only meter reading predates the report: wait for the next poll.
        assert box.plan.anchor is None
        assert box.plan.status == "waiting_for_soc"
        box.set(meter_at=NOW.timestamp(), total_kwh=100.5)
        box.plan.observe()
        await box.plan.tick()
        assert box.plan.anchor == SocAnchor(50.0, NOW, 100.5)
    run(go())


def test_a_reading_before_the_plug_in_counts_from_its_first_meter_reading(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = box.plan
        await plan.initialize()
        plan.observe()
        box.set(total_kwh=101.0, meter_at=NOW.timestamp() + 60)
        plan.observe()
        box.set(total_kwh=101.0, meter_at=NOW.timestamp() + 120)
        plan.observe()
        assert plan._trail == [(NOW.timestamp(), 100.0), (NOW.timestamp() + 60, 101.0)]
        # A report between the readings counts from the next one: the
        # projection errs towards charging more.
        assert plan._meter_at(NOW.timestamp() + 30, box.state) == 101.0
        assert plan._meter_at(NOW.timestamp() - 3600, box.state) == 100.0
        assert plan._meter_at(NOW.timestamp() + 90, box.state) == 101.0
        assert plan._meter_at(NOW.timestamp() + 200, box.state) is None
        # Another plug-in starts its own trail.
        box.set(scope=OTHER)
        plan.observe()
        assert plan._trail == [(NOW.timestamp() + 120, 101.0)]
        box.set(scope=None)
        plan.observe()
        assert plan._trail == []
    run(go())


def test_a_long_trail_thins_out_but_keeps_the_plug_in(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "_TRAIL_LIMIT", 4)

    async def go():
        box = Box(tmp_path)
        plan = box.plan
        await plan.initialize()
        for step in range(5):
            box.set(total_kwh=100.0 + step, meter_at=NOW.timestamp() + step)
            plan.observe()
        assert plan._trail == [(NOW.timestamp() + step, 100.0 + step) for step in (0, 2, 4)]
    run(go())


def test_unplugging_withdraws_the_plan_and_ends_its_own_pause(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        assert box.writes == [None]
        assert plan.plan_paused
        cancels = box.cancels
        box.set(present=False)
        await plan.tick()
        assert plan.status == "vehicle_unconfirmed"
        assert (box.resumes, box.cancels, plan.plan_paused, plan.anchor) == (1, cancels + 1, False, None)
        await plan.tick()
        assert box.resumes == 1
    run(go())


@pytest.mark.parametrize("owner", ["goal_stop", "order_active", "read_only"])
def test_a_pause_another_owner_holds_is_left_alone(tmp_path, owner):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        box.set(present=False, **{owner: True})
        await plan.tick()
        assert box.resumes == 0
        assert not plan.plan_paused
    run(go())


def test_home_assistant_withdrawing_its_confirmation_rests_the_plan(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        await plan.set_inputs(inputs(scope=None))
        await plan.tick()
        assert (plan.status, box.resumes, plan.confirmed) == ("vehicle_unconfirmed", 1, False)
        # A confirmation for an older plug-in does not count either.
        await plan.set_inputs(inputs(scope=OTHER))
        box.set(session=None)
        await plan.tick()
        assert plan.status == "vehicle_unconfirmed"
    run(go())


def test_another_car_brings_its_own_reading(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        assert plan.anchor is not None
        await plan.set_inputs(inputs(vehicle="profile:other:sensor.soc|"))
        assert plan.anchor is None
        await plan.tick()
        assert plan.status == "vehicle_unconfirmed"
    run(go())


def test_a_withdrawn_trip_ends_the_plans_pause(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=10)
        await plan.set_trip(None, None, withdraw=True)
        assert (box.resumes, plan.inputs, plan.plan_paused) == (1, None, False)
        assert plan.trip.snapshot() == {"deadline": None, "source": None, "finished": None}
        # The same trip again changes nothing.
        cancels = box.cancels
        await plan.set_trip(None, None)
        assert box.cancels == cancels
    run(go())


def test_the_same_plug_in_is_recognised_after_a_restart(tmp_path):
    async def go():
        box = Box(tmp_path)
        await box.start(hours=10)
        later = NOW + timedelta(minutes=10)
        restarted = Box(tmp_path, now=later, scope=OTHER, paused=True, pause_requested=True,
                        session=SessionMark(later, 4200, 5.0, 7, MARK.context))
        await restarted.plan.initialize()
        assert restarted.plan.plan_paused
        await restarted.plan.tick()
        assert (restarted.plan.status, restarted.plan.confirmed) == ("paused_until_start", True)
        assert restarted.writes == []
        assert restarted.resumes == 0
        # The wallbox has not reported the session yet: wait for it.
        unknown = Box(tmp_path, now=later, scope=OTHER, session=None)
        await unknown.plan.initialize()
        await unknown.plan.tick()
        assert unknown.plan.status == "waiting_for_device"
        assert unknown.resumes == 0
        # A new session counter is another plug-in.
        replugged = Box(tmp_path, now=later, scope=OTHER, pause_requested=True,
                        session=SessionMark(later, 60, 0.0, 8, MARK.context))
        await replugged.plan.initialize()
        await replugged.plan.tick()
        assert replugged.plan.status == "vehicle_unconfirmed"
        assert replugged.resumes == 1
    run(go())


def test_a_bus_gap_without_a_session_record_waits(tmp_path):
    async def go():
        box = Box(tmp_path, session=None)
        plan = await box.start()
        box.set(scope=None, present=None, now=NOW + timedelta(minutes=2))
        await plan.tick()
        assert plan.status == "waiting_for_device"
        assert box.resumes == 0
        # A new scope without any record of the confirmed plug-in is another one.
        box.set(scope=OTHER, present=True)
        await plan.tick()
        assert plan.status == "vehicle_unconfirmed"
    run(go())


def test_a_manual_command_waits_and_an_outdated_plan_command_is_cancelled(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=3)
        assert box.writes == [None]
        cancels = box.cancels
        box.set(pending=True)
        await plan.tick()
        # Our queued pause still fits; the queue keeps it.
        assert (plan.status, box.cancels) == ("queued", cancels)
        # A new reading needs charging at once: the queued pause goes.
        await plan.set_inputs(inputs(soc_percent=20, soc_observed_at=NOW - timedelta(minutes=5)))
        await plan.tick()
        assert box.cancels == cancels + 1
        assert plan.status == "queued"
        box.set(pending=False)
        await plan.tick()
        assert plan.status == "unreachable"
    run(go())


def test_cancelling_its_own_queued_command_lets_the_plan_go_on(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start(hours=3)
        cancel = box.cancel

        async def clears_queue():
            await cancel()
            box.set(pending=False)

        box.cancel = clears_queue
        plan._cancel = clears_queue
        box.set(pending=True)
        await plan.set_inputs(inputs(soc_percent=20, soc_observed_at=NOW - timedelta(minutes=5)))
        await plan.tick()
        assert plan.status == "unreachable"
    run(go())


def test_a_trip_reached_before_a_restart_plans_again_for_a_higher_target(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        box.set(goal_status="reached")
        await plan.tick()
        restarted = Box(tmp_path, target_percent=90.0)
        await restarted.plan.initialize()
        await restarted.plan.tick()
        # The target in force at the restart counts as the one reached.
        assert restarted.plan.trip.finished == "reached"
        restarted.set(target_percent=95.0)
        await restarted.plan.tick()
        assert restarted.plan.trip.finished is None
    run(go())


def test_an_estimated_stop_cancels_a_queued_plan_current(tmp_path):
    async def go():
        box = Box(tmp_path)
        plan = await box.start()
        cancels = box.cancels
        box.set(pending=True, total_kwh=125.0, meter_at=NOW.timestamp() + 60, now=NOW + timedelta(minutes=1))
        await plan.tick()
        assert box.cancels == cancels + 1
        assert plan.status == "estimated_reached"
    run(go())


def test_write_failures_defer_and_log_once(tmp_path, caplog):
    async def go():
        box = Box(tmp_path)
        box.fail = DeviceError("service_unavailable")
        with caplog.at_level(logging.WARNING, logger=dp.__name__):
            plan = await box.start()
            assert plan.status == "write_pending"
            box.clock[0] += 300
            await plan.tick()
            box.fail = AmtronBusError("lost")
            box.clock[0] += 300
            await plan.tick()
        assert caplog.text.count("(service_unavailable)") == 1
        assert caplog.text.count("(AmtronBusError)") == 1
        box.fail = None
        box.clock[0] += 300
        await plan.tick()
        assert plan.status == "charging"
        assert plan._last_error is None
    run(go())


def test_an_unknown_pause_state_or_close_current_writes_nothing(tmp_path):
    async def go():
        box = Box(tmp_path, paused=None)
        plan = await box.start()
        assert plan.status == "charging"
        assert box.writes == []
        box.set(paused=False, applied_a=15.5)
        await plan.tick()
        assert box.writes == []
    run(go())


def test_the_plan_survives_storage_failures(tmp_path, caplog, monkeypatch):
    async def go():
        box = Box(tmp_path)
        await box.plan.initialize()
        monkeypatch.setattr(box.plan.store, "save", lambda _plan: (_ for _ in ()).throw(OSError("full")))
        with caplog.at_level(logging.WARNING, logger=dp.__name__):
            await box.plan.set_trip(NOW + timedelta(hours=2), "manual")
            await box.plan.set_inputs(inputs())
        assert box.plan.storage_error
        assert caplog.text.count("could not be saved") == 1
        monkeypatch.undo()
        await box.plan.tick()
        assert not box.plan.storage_error
        assert DepartureStore(box.path, "fixture").load()["trip"]["deadline"] == (NOW + timedelta(hours=2)).isoformat()
    run(go())


@pytest.mark.parametrize("content", [
    b"{", b'{"version":1,"identity":"fixture","plan":{"trip":null}}',
    (b'{"version":1,"identity":"fixture","plan":{"trip":{"deadline":null,"source":null,"finished":null},'
    b'"inputs":null,"anchor":null,"session":null,"planned_start":null,"plan_paused":1,"estimated_stop":false}}'),
])
def test_an_unreadable_plan_waits_for_home_assistant(tmp_path, caplog, content):
    async def go():
        (tmp_path / "departure-plan.json").write_bytes(content)
        box = Box(tmp_path)
        with caplog.at_level(logging.WARNING, logger=dp.__name__):
            await box.plan.initialize()
        assert box.plan.ready
        assert box.plan.trip.deadline is None
        assert "Saved departure plan unavailable" in caplog.text
    run(go())


def test_commands_need_a_ready_plan_and_shutdown_saves(tmp_path):
    async def go():
        box = Box(tmp_path)
        with pytest.raises(DeviceError, match="service_unavailable"):
            await box.plan.set_trip(None, None)
        with pytest.raises(DeviceError, match="service_unavailable"):
            await box.plan.set_inputs(inputs())
        await box.start()
        await box.plan.shutdown()
        await box.plan.tick()
        box.plan.observe()
        with pytest.raises(DeviceError, match="service_unavailable"):
            await box.plan.set_trip(None, None)
        restored = box.controller()
        await restored.initialize()
        assert restored.inputs == inputs()
        assert restored.anchor == box.plan.anchor
    run(go())
