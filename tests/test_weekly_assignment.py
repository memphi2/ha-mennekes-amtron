"""A weekly plan follows the car Home Assistant detected, without a new consent."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import SessionMark
from custom_components.mennekes_amtron_rtu.device.weekly_controller import (
    WeeklyError,
    vehicle_key,
)
from custom_components.mennekes_amtron_rtu.device.weekly_plan import WeeklyPlan
from custom_components.mennekes_amtron_rtu.device.weekly_store import WeeklyBook
from tests.test_weekly_controller import NOW, PLAN, SCOPE, Harness, recipe

CAR = "profile:bmw:sensor.soc|"
NEXT = "c" * 64
TODAY, TOMORROW = NOW.date(), (NOW + timedelta(days=1)).date()
# The window of today's departure at 08:00 opens at 06:00.
OPEN = NOW.replace(hour=6)


class Garage(Harness):
    """The weekly harness with Home Assistant's detection beside it."""

    def __init__(self, path):
        super().__init__(path)
        self.car: tuple[str, str] | None = (CAR, SCOPE)
        self.weekly.confirmed = lambda: self.car

    async def assigned(self):
        await self.initialize()
        await self.save()
        await self.activate()
        return self

    async def both(self):
        await self.jobs.tick()
        await self.weekly.tick()

    async def replug(self, scope=NEXT, car=CAR, confirmed_for=NEXT):
        self.change(present=False, scope=None)
        await self.both()
        self.change(present=True, scope=scope)
        self.car = None if car is None else (car, confirmed_for)
        await self.both()


def test_a_profile_is_the_same_car_whatever_its_source_reads():
    assert vehicle_key(CAR) == "profile:bmw"
    assert vehicle_key("profile:bmw:vehicle:abc|maximum:abc") == "profile:bmw"
    assert vehicle_key("sensor.soc|sensor.limit") == "sensor.soc|sensor.limit"
    assert vehicle_key("profile:bmw") == "profile:bmw"


def test_a_car_that_returns_before_its_window_keeps_its_departure(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
        assert h.weekly.snapshot()["plans"][0]["assigned"] is True
        # The summary names the plan's own order.
        assert h.weekly.waiting()["job_id"] == h.jobs.job.identifier
        first = h.jobs.job.identifier
        assert h.weekly.book.used(PLAN, TODAY)
        writes = len(h.calls)
        for scope in (NEXT, "d" * 64, "e" * 64):
            await h.replug(scope, confirmed_for=scope)
            # The plan waits for the window: no order, no date, no write.
            assert (h.weekly.reason, h.weekly.consent.scope, h.weekly.consent.job_id) == (
                "waiting_next_occurrence", scope, None,
            )
            assert not h.weekly.book.used(PLAN, TODAY)
            assert h.jobs.job.identifier == first
            assert len(h.calls) == writes
            assert h.weekly.waiting() == {"plan_id": PLAN, "job_id": None, "start_at": OPEN.isoformat()}
        revision = h.weekly.revision
        await h.weekly.tick()
        assert h.weekly.revision == revision
        # At its window today's departure is charged for the new plug-in.
        h.change(now=OPEN + timedelta(seconds=1))
        await h.both()
        await h.jobs.tick()
        assert (h.weekly.reason, h.jobs.job.binding, h.jobs.job.state) == ("active", "e" * 64, "charging")
        assert h.weekly.book.used(PLAN, TODAY)
        assert not h.weekly.book.used(PLAN, TOMORROW)
    asyncio.run(run())


def test_a_departure_that_already_charged_is_not_repeated_after_a_replug(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        h.change(now=OPEN)
        await h.both()
        assert h.jobs.job.state == "charging"
        h.change(now=OPEN + timedelta(minutes=10))
        await h.replug()
        assert h.weekly.consent.job_id is None
        assert h.weekly.book.used(PLAN, TODAY)
        h.change(now=OPEN + timedelta(minutes=20))
        await h.both()
        assert h.weekly.reason == "waiting_next_occurrence"
        assert not h.weekly.book.used(PLAN, TOMORROW)
        h.change(now=OPEN + timedelta(days=1))
        await h.both()
        assert h.weekly.reason == "active"
        assert h.weekly.book.used(PLAN, TOMORROW)
    asyncio.run(run())


def test_without_a_detected_car_an_activation_remembers_nothing(tmp_path):
    async def run():
        h = Garage(tmp_path)
        h.car = (CAR, NEXT)
        await h.assigned()
        assert h.weekly.book.assigned == ()
        assert h.weekly.snapshot()["plans"][0]["assigned"] is False
        await h.replug()
        assert h.weekly.consent is None
    asyncio.run(run())


def test_an_activation_whose_order_failed_remembers_nothing(tmp_path, monkeypatch):
    async def run():
        h = Garage(tmp_path)
        await h.initialize()
        await h.save()

        async def refuse(*_args, **_kwargs):
            raise ValueError("fixture")

        monkeypatch.setattr(h.jobs, "create", refuse)
        with pytest.raises(ValueError, match="fixture"):
            await h.activate()
        assert (h.weekly.consent, h.weekly.book.assigned) == (None, ())
    asyncio.run(run())


@pytest.mark.parametrize("case", ["other_car", "undetected", "older_plug_in", "disabled"])
def test_only_the_detected_car_of_this_plug_in_is_served(tmp_path, case):
    async def run():
        h = await Garage(tmp_path).assigned()
        if case == "disabled":
            await h.save(enabled=False)
        await h.replug(
            car=None if case == "undetected" else "profile:vw:sensor.vw|" if case == "other_car" else CAR,
            confirmed_for=SCOPE if case == "older_plug_in" else NEXT,
        )
        assert h.weekly.consent is None
    asyncio.run(run())


def test_at_its_window_the_plan_waits_for_another_order_without_using_the_date(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.replug()
        h.change(now=OPEN, pending=True)
        await h.both()
        assert (h.weekly.reason, h.weekly.consent.job_id) == ("waiting_next_occurrence", None)
        # A queued command went through, but an order still holds the wallbox.
        h.change(pending=False)
        h.jobs.job.scope_ended = False
        assert h.jobs.holding
        await h.weekly.tick()
        assert h.weekly.consent.job_id is None
        assert not h.weekly.book.used(PLAN, TODAY)
        h.jobs.job.scope_ended = True
        await h.weekly.tick()
        assert h.weekly.reason == "active"
        assert h.weekly.book.used(PLAN, TODAY)
    asyncio.run(run())


def mark(after=timedelta(0), count=42):
    return SessionMark(NOW + after, 600 + int(after.total_seconds()), 2.0, count, "e" * 64)


async def restarted(tmp_path, *, count=42):
    first = Garage(tmp_path)
    first.change(session=mark())
    await first.assigned()
    job = first.jobs.job.identifier
    await first.weekly.shutdown()
    await first.jobs.shutdown()
    h = Garage(tmp_path)
    later = timedelta(minutes=5)
    h.change(now=NOW + later, scope=NEXT, session=mark(later, count))
    h.car = (CAR, NEXT)
    await h.initialize()
    return h, job


def test_a_restart_takes_the_plans_running_order_up_again(tmp_path):
    async def run():
        h, job = await restarted(tmp_path)
        assert h.weekly.consent is None
        # The suspended order decides first; the plan waits for it.
        await h.weekly.tick()
        assert h.weekly.consent is None
        await h.both()
        assert (h.weekly.reason, h.weekly.consent.job_id, h.weekly.consent.scope) == ("active", job, NEXT)
        assert h.jobs.job.identifier == job
        assert not h.weekly.book.used(PLAN, TOMORROW)
    asyncio.run(run())


def test_a_restart_without_proof_of_the_plug_in_still_serves_the_departure(tmp_path):
    async def run():
        h, job = await restarted(tmp_path, count=43)
        await h.both()
        assert h.jobs.job.state == "interrupted"
        # The order ended before its window: its date is free again.
        assert (h.weekly.reason, h.weekly.consent.job_id) == ("waiting_next_occurrence", None)
        assert not h.weekly.book.used(PLAN, TODAY)
        # At its window today's departure is charged for the new plug-in.
        h.change(now=OPEN + timedelta(seconds=1))
        await h.both()
        await h.jobs.tick()
        assert h.jobs.job.identifier == job
        assert (h.weekly.reason, h.jobs.job.state, h.jobs.job.binding) == ("active", "charging", NEXT)
    asyncio.run(run())


def test_another_running_order_is_waited_for(tmp_path):
    async def run():
        h, _job = await restarted(tmp_path)
        await h.jobs.tick()
        assert not h.jobs.job.terminal
        # The running order is not one of this plan's departures.
        own = h.weekly.book
        h.weekly.book = own.put(WeeklyPlan.parse(recipe(id="d" * 32, vehicle_profile="other"))).assign(
            "d" * 32, "profile:bmw")
        await h.weekly.tick()
        assert h.weekly.consent is None
        h.weekly.book = own
        await h.weekly.tick()
        assert h.weekly.consent is not None
    asyncio.run(run())


@pytest.mark.parametrize("how", ["plan", "charge", "idle"])
def test_a_stop_by_hand_forgets_the_car(tmp_path, how):
    async def run():
        h = await Garage(tmp_path).assigned()
        if how == "charge":
            await h.jobs.stop()
            await h.weekly.tick()
        else:
            if how == "idle":
                h.change(present=False, scope=None)
                await h.weekly.tick()
                assert h.weekly.consent is None
                assert h.weekly.book.assigned != ()
            result = await h.weekly.deactivate(revision=h.weekly.revision)
            assert result["plans"][0]["assigned"] is False
        assert h.weekly.book.assigned == ()
        assert WeeklyBook.parse(h.weekly.store.load().snapshot()).assigned == ()
        await h.replug()
        assert h.weekly.consent is None
    asyncio.run(run())


@pytest.mark.parametrize("own", [True, False])
def test_a_stop_by_hand_before_the_plan_took_its_order_up_again_counts_too(tmp_path, own):
    async def run():
        h, _job = await restarted(tmp_path)
        if not own:
            h.weekly.book = h.weekly.book.put(WeeklyPlan.parse(recipe(id="d" * 32, vehicle_profile="other"))).assign(
                "d" * 32, "profile:bmw")
        await h.jobs.stop()
        h.change(current_a=1)
        await h.both()
        await h.both()
        # Only the plan whose order was stopped forgets its car.
        assert (h.weekly.consent is None) is own
        assert h.weekly.book.assigned == (() if own else (("d" * 32, "profile:bmw"),))
        assert not h.weekly.book.used(PLAN, TOMORROW)
    asyncio.run(run())


def test_a_failed_save_after_a_stop_by_hand_fences_the_plan(tmp_path, monkeypatch):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.jobs.stop()
        monkeypatch.setattr(h.weekly.store, "save", lambda _book: (_ for _ in ()).throw(OSError("fixture")))
        await h.weekly.tick()
        assert (h.weekly.consent, h.weekly.storage_error) == (None, True)
        await h.replug()
        assert h.weekly.consent is None
        with pytest.raises(WeeklyError, match="weekly_storage_unavailable"):
            await h.weekly.deactivate(revision=h.weekly.revision)
    asyncio.run(run())


def test_once_the_disk_works_again_the_stop_is_saved_and_the_plans_work(tmp_path, monkeypatch):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.jobs.stop()
        monkeypatch.setattr(h.weekly.store, "save", lambda _book: (_ for _ in ()).throw(OSError("fixture")))
        await h.weekly.tick()
        assert h.weekly.storage_error
        monkeypatch.undo()
        await h.replug()
        # The file reads back; the stop by hand is saved before any plan is
        # served, so the car is not served again.
        assert not h.weekly.storage_error
        assert h.weekly.book.assigned == () == h.weekly.store.load().assigned
        assert h.weekly.consent is None
        await h.save(charge_minutes=90)
        assert h.weekly.book.plan(PLAN).charge_minutes == 90
    asyncio.run(run())


def test_stopping_the_plan_ends_it_even_when_forgetting_the_car_fails(tmp_path, monkeypatch):
    async def run():
        h = await Garage(tmp_path).assigned()
        monkeypatch.setattr(h.weekly.store, "save", lambda _book: (_ for _ in ()).throw(OSError("fixture")))
        with pytest.raises(WeeklyError, match="weekly_storage_unavailable"):
            await h.weekly.deactivate(revision=h.weekly.revision)
        assert (h.weekly.consent, h.jobs.job.state) == (None, "stopped")
    asyncio.run(run())


def test_a_failed_release_or_start_is_not_repeated_for_the_plug_in(tmp_path, monkeypatch):
    async def run():
        h = await Garage(tmp_path).assigned()

        async def refuse(*_args, **_kwargs):
            raise ValueError("fixture")

        await h.replug()
        monkeypatch.setattr(h.jobs, "create", refuse)
        h.change(now=OPEN + timedelta(seconds=1))
        await h.both()
        assert (h.weekly.consent, h.weekly.reason) == (None, "activation_unconfirmed")
        monkeypatch.undo()
        await h.weekly.tick()
        assert h.weekly.consent is None
        # A date that cannot be freed leaves the plan inactive.
        other = await Garage(tmp_path / "other").assigned()
        other.change(present=False, scope=None)
        await other.both()
        monkeypatch.setattr(other.weekly.store, "save", lambda _book: (_ for _ in ()).throw(OSError("fixture")))
        other.change(present=True, scope=NEXT)
        other.car = (CAR, NEXT)
        await other.both()
        assert (other.weekly.consent, other.weekly.storage_error) == (None, True)
    asyncio.run(run())


def test_an_edited_plan_waits_for_the_next_plug_in_and_keeps_its_car(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.save(current_a=10)
        assert (h.weekly.consent, h.weekly.reason) == (None, "plan_changed")
        await h.both()
        # The plan's own stop is not a stop by hand.
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
        assert h.weekly.consent is None
        await h.replug()
        assert h.weekly.consent.scope == NEXT
        # A plan that now names another car no longer has the remembered one.
        await h.save(vehicle_profile="zoe")
        assert h.weekly.book.assigned == ()
    asyncio.run(run())


def test_an_assigned_car_that_stays_plugged_in_is_served_past_seven_days(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.complete()
        h.change(now=NOW + timedelta(days=7, hours=1))
        await h.weekly.tick()
        assert (h.weekly.consent, h.weekly.reason) == (None, "session_ended")
        await h.weekly.tick()
        assert (h.weekly.reason, h.weekly.consent.scope) == ("waiting_next_occurrence", SCOPE)
        assert h.weekly.consent.expires == NOW + timedelta(days=14, hours=1)
    asyncio.run(run())


def test_one_car_has_one_plan_and_a_removed_plan_loses_it():
    other = "d" * 32
    book = WeeklyBook().put(WeeklyPlan.parse(recipe())).put(WeeklyPlan.parse(recipe(id=other, vehicle_profile="second")))
    book = book.assign(PLAN, "profile:bmw").assign(other, "profile:vw")
    assert book.assigned_plan("profile:vw") == other
    moved = book.assign(PLAN, "profile:vw")
    assert moved.assigned == ((PLAN, "profile:vw"),)
    assert book.unassign(other).assigned == ((PLAN, "profile:bmw"),)
    assert book.unassign(None).assigned == ()
    assert book.remove(PLAN).assigned == ((other, "profile:vw"),)
    assert book.put(WeeklyPlan.parse(recipe(current_a=10))).assigned == book.assigned
    assert WeeklyBook.parse(book.snapshot()) == book
    assert book.assigned_plan("profile:none") is None
    with pytest.raises(ValueError, match="not found"):
        book.assign("e" * 32, "profile:bmw")


def test_only_the_latest_used_date_can_be_freed():
    book = WeeklyBook().put(WeeklyPlan.parse(recipe())).reserve(PLAN, TOMORROW)
    with pytest.raises(ValueError, match="latest"):
        book.release(PLAN, TODAY)
    freed = book.release(PLAN, TOMORROW)
    assert (freed.used(PLAN, TODAY), freed.used(PLAN, TOMORROW)) == (True, False)


@pytest.mark.parametrize("assigned", [
    [], {"e" * 32: "profile:bmw"}, {PLAN: ""}, {PLAN: 5}, {PLAN: "x" * 513},
    {PLAN: "profile:bmw", "d" * 32: "profile:bmw"},
])
def test_a_saved_assignment_needs_a_known_plan_and_one_car_each(assigned):
    plans = [recipe(), recipe(id="d" * 32, vehicle_profile="second")]
    with pytest.raises(ValueError, match="assignment"):
        WeeklyBook.parse({"plans": plans, "consumed": {}, "assigned": assigned})
    # A book saved without assignments still loads.
    assert WeeklyBook.parse({"plans": plans, "consumed": {}}).assigned == ()


def test_a_car_that_left_before_its_window_is_charged_when_it_returns_inside_it(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        assert h.weekly.book.used(PLAN, TODAY)
        h.change(present=False, scope=None)
        # Even when the plan notices the unplugging first, the order ends as
        # interrupted, not as stopped by hand.
        await h.weekly.tick()
        await h.both()
        assert h.jobs.job.state == "interrupted"
        await h.both()
        # The date is free again while the window is still ahead.
        assert not h.weekly.book.used(PLAN, TODAY)
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
        revision = h.weekly.revision
        await h.both()
        assert h.weekly.revision == revision
        h.change(now=OPEN + timedelta(minutes=10), present=True, scope=NEXT)
        h.car = (CAR, NEXT)
        await h.both()
        await h.both()
        await h.jobs.tick()
        assert (h.weekly.reason, h.jobs.job.binding, h.jobs.job.state) == ("active", NEXT, "charging")
        assert h.weekly.book.used(PLAN, TODAY)
    asyncio.run(run())


@pytest.mark.parametrize("how", ["clear", "stop"])
def test_a_waiting_plan_keeps_its_car_when_another_order_is_stopped_or_cleared(tmp_path, how):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.replug()
        assert (h.weekly.reason, h.weekly.consent.job_id) == ("waiting_next_occurrence", None)
        if how == "clear":
            # The old, interrupted order is dismissed.
            await h.jobs.clear(revision=h.jobs.revision)
        else:
            h.jobs.manual_stop_epoch += 1
        await h.both()
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
        assert (h.weekly.reason, h.weekly.consent.job_id) == ("waiting_next_occurrence", None)
        # At its window the plan still charges.
        h.change(now=OPEN + timedelta(seconds=1))
        await h.both()
        await h.jobs.tick()
        assert (h.weekly.reason, h.jobs.job.state) == ("active", "charging")
    asyncio.run(run())


def test_clearing_the_plans_finished_order_ends_the_activation_but_keeps_the_car(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        h.change(now=OPEN + timedelta(hours=3))
        await h.both()
        assert h.jobs.job.terminal
        await h.jobs.clear(revision=h.jobs.revision)
        await h.both()
        assert (h.weekly.consent, h.weekly.reason) == (None, "session_ended")
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
    asyncio.run(run())
