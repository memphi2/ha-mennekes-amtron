"""A weekly plan follows the car Home Assistant confirmed, without a new consent."""

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


class Garage(Harness):
    """The weekly harness with Home Assistant's confirmation beside it."""

    def __init__(self, path):
        super().__init__(path)
        self.car: tuple[str, str] | None = (CAR, SCOPE)
        self.weekly.confirmed = lambda: self.car

    async def assigned(self):
        await self.initialize()
        await self.save()
        await self.activate()
        return self

    async def replug(self, scope=NEXT, car=CAR, confirmed_for=NEXT):
        self.change(present=False, scope=None)
        await self.jobs.tick()
        await self.weekly.tick()
        self.change(present=True, scope=scope)
        self.car = None if car is None else (car, confirmed_for)
        await self.jobs.tick()
        await self.weekly.tick()


def test_a_profile_is_the_same_car_whatever_its_source_reads():
    assert vehicle_key(CAR) == "profile:bmw"
    assert vehicle_key("profile:bmw:vehicle:abc|maximum:abc") == "profile:bmw"
    assert vehicle_key("sensor.soc|sensor.limit") == "sensor.soc|sensor.limit"
    assert vehicle_key("profile:bmw") == "profile:bmw"


def test_an_activation_remembers_the_confirmed_car_and_serves_its_next_plug_in(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
        assert h.weekly.snapshot()["plans"][0]["assigned"] is True
        first = h.jobs.job.identifier
        await h.replug()
        assert h.weekly.reason == "active"
        assert (h.weekly.consent.scope, h.weekly.consent.plan_id) == (NEXT, PLAN)
        # Today's departure was used; the next one is planned for the new plug-in.
        assert h.jobs.job.identifier != first
        assert h.jobs.job.binding == NEXT
        assert h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
        revision = h.weekly.revision
        await h.weekly.tick()
        assert h.weekly.revision == revision
    asyncio.run(run())


def test_without_a_confirmed_car_an_activation_remembers_nothing(tmp_path):
    async def run():
        h = Garage(tmp_path)
        h.car = (CAR, NEXT)
        await h.assigned()
        assert h.weekly.book.assigned == ()
        assert h.weekly.snapshot()["plans"][0]["assigned"] is False
        await h.replug()
        assert h.weekly.consent is None
    asyncio.run(run())


@pytest.mark.parametrize("case", ["other_car", "unconfirmed", "older_plug_in", "disabled", "busy"])
def test_only_the_confirmed_car_of_this_plug_in_is_served(tmp_path, case):
    async def run():
        h = await Garage(tmp_path).assigned()
        if case == "disabled":
            await h.save(enabled=False)
        if case == "busy":
            h.change(pending=True)
        await h.replug(
            car=None if case == "unconfirmed" else "profile:vw:sensor.vw|" if case == "other_car" else CAR,
            confirmed_for=SCOPE if case == "older_plug_in" else NEXT,
        )
        assert h.weekly.consent is None
        if case == "busy":
            # The queued command went through: the plug-in is served after all.
            h.change(pending=False)
            await h.weekly.tick()
            assert h.weekly.consent.scope == NEXT
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
        await h.jobs.tick()
        await h.weekly.tick()
        assert (h.weekly.reason, h.weekly.consent.job_id, h.weekly.consent.scope) == ("active", job, NEXT)
        assert h.jobs.job.identifier == job
        assert not h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
    asyncio.run(run())


def test_a_restart_without_proof_of_the_plug_in_plans_the_next_departure(tmp_path):
    async def run():
        h, job = await restarted(tmp_path, count=43)
        await h.jobs.tick()
        assert h.jobs.job.state == "interrupted"
        # The interrupted order is cleared by hand first; no date is used up.
        await h.weekly.tick()
        assert h.weekly.consent is None
        assert not h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
        await h.jobs.clear(revision=h.jobs.revision)
        await h.weekly.tick()
        assert (h.weekly.reason, h.weekly.consent.scope) == ("active", NEXT)
        assert h.jobs.job.identifier != job
        assert h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
    asyncio.run(run())


def test_another_running_order_is_waited_for(tmp_path):
    async def run():
        h, _job = await restarted(tmp_path)
        await h.jobs.tick()
        assert not h.jobs.job.terminal
        # The running order is not one of this plan's departures.
        own = h.weekly.book
        h.weekly.book = own.put(WeeklyPlan.parse(recipe(vehicle_profile="other")))
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


def test_a_plug_in_is_served_once_and_a_failed_start_is_not_repeated(tmp_path, monkeypatch):
    async def run():
        h = await Garage(tmp_path).assigned()

        async def refuse(*_args, **_kwargs):
            raise ValueError("fixture")

        h.change(present=False, scope=None)
        await h.jobs.tick()
        await h.weekly.tick()
        monkeypatch.setattr(h.jobs, "create", refuse)
        h.change(present=True, scope=NEXT)
        h.car = (CAR, NEXT)
        await h.jobs.tick()
        await h.weekly.tick()
        assert (h.weekly.consent, h.weekly.reason) == (None, "activation_unconfirmed")
        monkeypatch.undo()
        await h.weekly.tick()
        assert h.weekly.consent is None
    asyncio.run(run())


def test_an_edited_plan_waits_for_the_next_plug_in(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.save(current_a=10)
        assert (h.weekly.consent, h.weekly.reason) == (None, "plan_changed")
        assert h.weekly.book.assigned == ((PLAN, "profile:bmw"),)
        await h.jobs.tick()
        await h.weekly.tick()
        assert h.weekly.consent is None
        await h.replug()
        assert h.weekly.consent.scope == NEXT
    asyncio.run(run())


def test_an_assigned_car_that_stays_plugged_in_is_served_past_seven_days(tmp_path):
    async def run():
        h = await Garage(tmp_path).assigned()
        await h.complete()
        h.change(now=NOW + timedelta(days=7, hours=1))
        await h.weekly.tick()
        assert (h.weekly.consent, h.weekly.reason) == (None, "session_ended")
        await h.weekly.tick()
        assert (h.weekly.reason, h.weekly.consent.scope) == ("active", SCOPE)
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
    assert WeeklyBook.parse(book.snapshot()) == book
    assert book.assigned_plan("profile:none") is None
    with pytest.raises(ValueError, match="not found"):
        book.assign("e" * 32, "profile:bmw")


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
