"""A recurring recipe cannot override manual intent, replay or vehicle scope."""

from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import SessionMark
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobInputs,
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_store import ChargeJobStore
from custom_components.mennekes_amtron_rtu.device.weekly_controller import (
    WeeklyController,
    WeeklyError,
)
from custom_components.mennekes_amtron_rtu.device.weekly_jobs import WeeklyChargeJobs
from custom_components.mennekes_amtron_rtu.device.weekly_store import WeeklyStore

NOW = datetime(2026, 11, 16, 5, tzinfo=UTC)
SCOPE = "b" * 64
PLAN = "a" * 32


def recipe(**changes):
    return {"id": PLAN, "vehicle_profile": "fixture", "timezone": "UTC", "weekdays": list(range(7)),
            "departure_time": "08:00", "charge_minutes": 120, "current_a": 12, "energy_kwh": 10, **changes}


class Harness:
    def __init__(self, path):
        self.sample = JobInputs(NOW, True, True, SCOPE, 100.0, True, True, False, 8.0, 16.0)
        self.calls = []
        self.jobs = WeeklyChargeJobs(ChargeJobStore(path / "charge-job.json", "fixture"), lambda: self.sample, self.apply)
        self.weekly = WeeklyController(WeeklyStore(path / "weekly-plans.json", "fixture"), self.jobs)

    def change(self, **changes):
        self.sample = replace(self.sample, **changes)

    async def apply(self, value, owner, deadline):
        self.calls.append((value, owner, deadline))
        self.change(current_a=1 if value is None else value)

    async def initialize(self):
        await self.jobs.initialize()
        await self.weekly.initialize()

    async def save(self, **changes):
        return await self.weekly.save_plan(recipe(**changes), revision=self.weekly.revision)

    def preview(self, **changes):
        return self.weekly.preview_activation(PLAN, "fixture", revision=self.weekly.revision, scope=SCOPE, **changes)

    async def activate(self):
        return await self.weekly.activate(self.preview())

    async def complete(self):
        self.change(now=NOW.replace(hour=6))
        await self.jobs.tick()
        self.change(total_kwh=110, now=NOW.replace(hour=7))
        await self.jobs.tick()
        await self.weekly.tick()


def test_recipe_is_not_permission_and_two_days_use_the_same_single_job_owner(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        p = h.weekly.preview_plan(recipe(), revision=h.weekly.revision)
        assert p["plan"]["vehicle_profile"] == "fixture"
        assert h.calls == []
        assert not h.weekly.store.path.exists()
        await h.save()
        await h.weekly.tick()
        assert h.calls == []
        proposal = h.preview()
        assert not h.jobs.store.path.exists()
        result = await h.weekly.activate(proposal)
        assert result["active"]["plan_id"] == PLAN
        first = h.jobs.job.identifier
        assert h.calls[-1][0] is None
        assert h.weekly.book.used(PLAN, NOW.date())
        await h.complete()
        assert h.jobs.job.state == "reached"
        assert h.weekly.consent is not None
        assert h.jobs.job.identifier == first
        h.change(now=NOW.replace(hour=8))
        await h.weekly.tick()
        # The next order waits for its own window, not for the previous one.
        assert h.jobs.job.identifier == first
        assert h.weekly.reason == "waiting_next_occurrence"
        assert not h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
        h.change(now=(NOW + timedelta(days=1)).replace(hour=6))
        await h.weekly.tick()
        second = h.jobs.job
        assert second.identifier != first
        assert second.energy.baseline_kwh == 110
        assert second.state == "charging"
        assert h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
        assert h.calls[-1][0] == 12
        assert h.jobs.job.identifier == second.identifier
        # A replayed activation response reports the new outcome; it never
        # replaces the successor or refreshes either meter baseline.
        assert (await h.weekly.activate(proposal))["job"]["id"] == second.identifier
        assert h.jobs.job.energy.baseline_kwh == 110
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "clear"])
def test_manual_stop_even_after_completion_cancels_future_recurrence(tmp_path, action):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        preview = h.preview()
        await h.weekly.activate(preview)
        await h.complete()
        await getattr(h.jobs, action)(revision=h.jobs.revision)
        before = list(h.calls)
        h.change(now=NOW.replace(hour=8))
        await h.weekly.tick()
        assert h.weekly.consent is None
        assert (await h.weekly.activate(preview))["active"] is None
        assert not any(value is not None for value, _owner, _deadline in h.calls[len(before):])
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"scope": "c" * 64}, {"scope": None}, {"present": False},
    {"independent_stop": True}, {"permitted": False}, {"now": NOW - timedelta(seconds=6)},
    {"now": NOW + timedelta(days=7)},
])
def test_activation_never_outlives_its_session_permission(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        h.change(**change)
        await h.weekly.tick()
        assert h.weekly.consent is None
        assert h.jobs.job.terminal
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"available": False}, {"present": None}])
def test_a_short_gap_keeps_the_activation(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        h.change(**change)
        await h.weekly.tick()
        # The order fences its writes meanwhile; only a lasting gap ends it,
        # and that also ends the connection scope.
        assert h.weekly.consent is not None
        assert not h.jobs.job.terminal
        h.change(available=True, present=True)
        await h.weekly.tick()
        assert h.weekly.consent is not None
    asyncio.run(run())


@pytest.mark.parametrize("action", ["save", "remove", "deactivate", "shutdown"])
def test_edit_and_removal_stop_only_the_owned_job(tmp_path, action):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        if action == "save":
            assert h.weekly.preview_plan(recipe(), revision=h.weekly.revision)["will_stop_active"]
            await h.save(current_a=16)
        elif action == "remove":
            await h.weekly.remove_plan(PLAN, revision=h.weekly.revision)
            assert h.weekly.book.plans == ()
        elif action == "deactivate":
            await h.weekly.deactivate(revision=h.weekly.revision)
        else:
            await h.weekly.shutdown()
        assert h.weekly.consent is None
        assert h.weekly.book.used(PLAN, NOW.date())
        if action != "shutdown":
            assert h.jobs.job.state == "stopped"
            return
        # A restart is no stop: the job owner suspends or interrupts its order.
        assert h.jobs.job.state == "scheduled"
        await h.jobs.shutdown()
        assert (h.jobs.job.state, h.jobs.job.reason) == ("interrupted", "service_restarted")
        assert h.jobs.manual_stop_epoch == 0
    asyncio.run(run())


def test_edit_another_vehicle_does_not_stop_active_vehicle(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.save(id="c" * 32, vehicle_profile="other")
        assert h.weekly.consent is not None
        await h.weekly.remove_plan("c" * 32, revision=h.weekly.revision)
        assert h.weekly.consent is not None
    asyncio.run(run())


@pytest.mark.parametrize("revision", [None, True, "stale"])
def test_invalid_revision_has_no_write(tmp_path, revision):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        with pytest.raises(WeeklyError, match="weekly_changed"):
            await h.weekly.save_plan(recipe(), revision=revision)
        with pytest.raises(JobRequestError, match="job_changed"):
            await h.jobs.stop(revision=revision if revision is not None else "stale")
        with pytest.raises(JobRequestError, match="job_changed"):
            await h.jobs.clear(revision=revision)
        assert h.jobs.manual_stop_epoch == 0
        assert not h.weekly.store.path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["signature", "request", "missing", "late", "future", "scope", "revision"])
def test_confirmation_is_frozen_signed_expiring_and_session_bound(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        p = h.preview()
        if change == "signature":
            p["confirmation"] = "x"
        elif change == "request":
            p["request"]["energy_kwh"] = 50
        elif change == "missing":
            p = {}
        elif change == "late":
            h.change(now=NOW + timedelta(seconds=61))
        elif change == "future":
            h.change(now=NOW - timedelta(seconds=1))
        elif change == "scope":
            h.change(scope="c" * 64)
        else:
            await h.save(current_a=16)
        with pytest.raises(WeeklyError, match="weekly_"):
            await h.weekly.activate(p)
        assert h.jobs.job is None
        assert h.calls == []
    asyncio.run(run())


@pytest.mark.parametrize("case", ["profile", "scope", "disabled", "used", "busy", "active"])
def test_preview_refuses_wrong_or_competing_owners(tmp_path, case):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save(enabled=case != "disabled")
        profile, scope = "fixture", SCOPE
        if case == "profile":
            profile = "other"
        elif case == "scope":
            scope = None
        elif case == "used":
            h.weekly.book = h.weekly.book.reserve(PLAN, (NOW + timedelta(days=20)).date())
        elif case in ("busy", "active"):
            await h.activate()
            if case == "busy":
                h.weekly.consent = None
        with pytest.raises((WeeklyError, JobRequestError), match=r"weekly_|job_exists"):
            h.weekly.preview_activation(PLAN, profile, revision=h.weekly.revision, scope=scope)
    asyncio.run(run())


def test_restart_restores_recipes_and_consumption_but_not_consent(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        other = Harness(tmp_path)
        await other.initialize()
        assert other.weekly.consent is None
        assert other.weekly.book.used(PLAN, NOW.date())
        assert other.jobs.job.reason == "service_restarted"
        await other.weekly.tick()
        assert other.calls == []
    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError("storage fixture"), ValueError("storage fixture")])
def test_failed_consumption_save_never_creates_a_job(tmp_path, monkeypatch, error):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        preview = h.preview()
        monkeypatch.setattr(h.weekly.store, "save", Mock(side_effect=error))
        with pytest.raises(WeeklyError, match="weekly_storage_unavailable"):
            await h.weekly.activate(preview)
        assert h.jobs.job is None
        assert h.weekly.consent is None
        assert h.calls == []
        with pytest.raises(WeeklyError, match="weekly_storage_unavailable"):
            h.preview()
    asyncio.run(run())


def test_stop_during_consumption_fsync_never_rearms(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        preview = h.preview()
        original = h.weekly.store.save

        def save(book):
            h.jobs.manual_stop_epoch += 1
            original(book)

        monkeypatch.setattr(h.weekly.store, "save", save)
        with pytest.raises(WeeklyError, match="weekly_manual_stop"):
            await h.weekly.activate(preview)
        assert h.weekly.book.used(PLAN, NOW.date())
        assert h.jobs.job is None
    asyncio.run(run())


def test_concurrent_duplicate_confirmation_creates_one_baseline(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        p = h.preview()
        first, second = await asyncio.gather(h.weekly.activate(p), h.weekly.activate(deepcopy(p)))
        assert first["job"]["id"] == second["job"]["id"]
        assert len(h.calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"pending": True}, {"meter_fresh": False}, {"permitted": False}, {"independent_stop": True},
])
def test_next_window_waits_for_meter_queue_and_independent_stops(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.complete()
        old = h.jobs.job.identifier
        # Between departures Home Assistant may pause, stop or lock; that is
        # its own control and neither ends the plan nor is overridden by it.
        h.change(now=(NOW + timedelta(days=1)).replace(hour=6), **change)
        await h.weekly.tick()
        assert h.jobs.job.identifier == old
        assert h.weekly.consent is not None
        assert not h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
        h.change(pending=False, meter_fresh=True, permitted=True, independent_stop=False,
                 now=(NOW + timedelta(days=1)).replace(hour=6, minute=30))
        await h.weekly.tick()
        assert h.jobs.job.identifier != old
        assert h.calls[-1][0] == 12
    asyncio.run(run())


def test_no_next_occurrence_within_consent_keeps_a_visible_wait(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save(weekdays=[0])
        await h.activate()
        await h.complete()
        h.change(now=NOW.replace(hour=8))
        await h.weekly.tick()
        assert h.weekly.reason == "waiting_next_occurrence"
    asyncio.run(run())


def test_unreadable_storage_and_uninitialized_controller_are_not_permission(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        with pytest.raises(WeeklyError, match="weekly_storage_unavailable"):
            h.weekly.preview_plan(recipe(), revision=h.weekly.revision)
        monkeypatch.setattr(h.weekly.store, "load", Mock(side_effect=ValueError("fixture")))
        await h.weekly.initialize()
        assert h.weekly.ready
        assert h.weekly.storage_error
        assert h.weekly.reason == "weekly_storage_unavailable"
        with pytest.raises(WeeklyError, match="weekly_inactive"):
            await h.weekly._dispatch({}, "2026-11-16", h.jobs.revision)
    asyncio.run(run())


@pytest.mark.parametrize("case", ["storage", "closing", "missing", "replaced", "stopped", "interrupted"])
def test_recurrence_ends_on_unknown_or_replaced_owner(tmp_path, case):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        if case == "storage":
            h.weekly.storage_error = True
        elif case == "closing":
            h.weekly._closing = True
        elif case == "missing":
            h.jobs.job = None
        elif case == "replaced":
            h.jobs.job.identifier = "c" * 32
        elif case == "stopped":
            h.jobs.job.stop()
        else:
            h.jobs.job.interrupt("clock_changed")
        await h.weekly.tick()
        assert h.weekly.consent is None
    asyncio.run(run())


def test_successor_failure_consumes_the_date_and_disarms(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.complete()
        h.change(now=(NOW + timedelta(days=1)).replace(hour=6), current_cap=6)
        await h.weekly.tick()
        assert h.weekly.consent is None
        assert h.weekly.reason == "activation_unconfirmed"
        assert h.weekly.book.used(PLAN, (NOW + timedelta(days=1)).date())
    asyncio.run(run())


def test_cancelled_storage_save_finishes_before_permission_is_discarded(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        p = h.preview()
        started, release = threading.Event(), threading.Event()
        original = h.weekly.store.save

        def save(book):
            started.set()
            assert release.wait(5)
            original(book)

        monkeypatch.setattr(h.weekly.store, "save", save)
        task = asyncio.create_task(h.weekly.activate(p))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.weekly.consent is None
        assert h.weekly.storage_error
        assert h.jobs.job is None
        assert h.weekly.store.load().used(PLAN, NOW.date())
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": False}, {"scope": "c" * 64}, {"meter_fresh": False},
    {"permitted": False}, {"independent_stop": True}, {"pending": True},
    {"now": NOW.replace(hour=7)},
])
def test_atomic_successor_rechecks_all_stop_and_readback_conditions(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.complete()
        h.change(now=NOW.replace(hour=8))
        h.change(**change)
        with pytest.raises(JobRequestError, match="weekly_successor_not_permitted"):
            await h.jobs.successor({}, revision=h.jobs.revision, scope=SCOPE,
                                   predecessor=h.jobs.job.identifier, stop_epoch=h.jobs.manual_stop_epoch)
    asyncio.run(run())


@pytest.mark.parametrize("case", ["same_id", "guest", "cap", "save", "epoch", "owner", "startup", "storage", "closing", "unplugged", "unfinished"])
def test_successor_is_not_an_alternate_general_create_api(tmp_path, monkeypatch, case):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.complete()
        h.change(now=NOW.replace(hour=8))
        old, epoch = h.jobs.job.identifier, h.jobs.manual_stop_epoch
        request = {"id": "c" * 32, "start_at": h.sample.now.isoformat(),
                   "finish_at": (h.sample.now + timedelta(hours=1)).isoformat(), "current_a": 12, "energy_kwh": 10}
        if case == "same_id":
            request["id"] = old
        elif case == "guest":
            request["kind"] = "guest"
        elif case == "cap":
            request["current_a"] = 32
        elif case == "save":
            monkeypatch.setattr(h.jobs.store, "save", Mock(side_effect=OSError("fixture")))
        elif case == "epoch":
            epoch -= 1
        elif case == "owner":
            old = "d" * 32
        elif case == "startup":
            h.jobs.ready = False
        elif case == "storage":
            h.jobs.storage_error = True
        elif case == "closing":
            h.jobs._closing = True
        elif case == "unplugged":
            h.jobs.job.scope_ended = True
        else:
            h.jobs.job.state = "stopped"
        with pytest.raises(JobRequestError, match=r"weekly_successor|job_current|job_storage"):
            await h.jobs.successor(request, revision=h.jobs.revision, scope=SCOPE, predecessor=old, stop_epoch=epoch)
    asyncio.run(run())


def test_a_finished_weekly_order_hands_control_back_until_the_next_window(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        first = h.jobs.job.identifier
        await h.complete()
        assert h.jobs.job.state == "reached"
        await h.jobs.tick()
        assert not h.jobs.holding
        assert not h.jobs.stop_requested()
        assert h.jobs.current_policy("ha-solar", True) is None
        assert h.jobs.store.load() is None
        # Home Assistant charges with its own current meanwhile.
        h.change(now=NOW.replace(hour=12), current_a=16)
        await h.jobs.tick()
        await h.weekly.tick()
        assert h.jobs.job.identifier == first
        assert h.weekly.consent is not None
        h.change(now=(NOW + timedelta(days=1)).replace(hour=5, minute=59))
        await h.weekly.tick()
        assert h.jobs.job.identifier == first
        # Its window opens: the next order takes over without a prior pause.
        h.change(now=(NOW + timedelta(days=1)).replace(hour=6))
        await h.weekly.tick()
        second = h.jobs.job.identifier
        assert second != first
        assert h.jobs.holding
        assert h.jobs.store.load().identifier == second
        assert h.calls[-1][0] == 12
        assert h.jobs.current_policy("ha-solar", True) is False
    asyncio.run(run())


def test_the_last_weekly_order_and_a_stopped_plan_hand_control_back(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        # Only Mondays: the next one lies beyond the seven-day activation.
        await h.save(weekdays=[0])
        await h.activate()
        await h.complete()
        await h.jobs.tick()
        assert not h.jobs.holding
        assert h.jobs.store.load() is None
        h.change(now=NOW.replace(hour=8))
        await h.weekly.tick()
        assert h.weekly.reason == "waiting_next_occurrence"
        other = Harness(tmp_path / "other")
        await other.initialize()
        await other.save()
        await other.activate()
        await other.weekly.deactivate(revision=other.weekly.revision)
        assert other.jobs.job.state == "stopped"
        await other.jobs.tick()
        assert not other.jobs.holding
        assert other.jobs.store.load() is None
    asyncio.run(run())


def test_a_new_manual_order_between_departures_ends_the_plan(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.complete()
        await h.jobs.tick()
        h.change(now=NOW.replace(hour=9))
        request = {"id": "d" * 32, "start_at": h.sample.now.isoformat(),
                   "finish_at": (h.sample.now + timedelta(hours=1)).isoformat(), "current_a": 10, "energy_kwh": 5}
        p = h.jobs.preview(request, revision=h.jobs.revision, scope=SCOPE)
        await h.jobs.create(p["request"], revision=p["revision"], scope=SCOPE)
        await h.weekly.tick()
        assert h.weekly.consent is None
        assert h.weekly.reason == "job_stopped"
        # The plan never stops an order it does not own.
        assert h.jobs.job.state == "charging"
    asyncio.run(run())


@pytest.mark.parametrize("proven", [True, False])
def test_a_lasting_gap_moves_the_plan_to_a_proven_plug_in_or_ends_it(tmp_path, proven):
    def mark(after, count=42):
        return SessionMark(NOW + after, 600 + int(after.total_seconds()), 2.0, count, "e" * 64)

    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        h.change(session=mark(timedelta(0)))
        await h.activate()
        h.change(now=NOW.replace(hour=6))
        await h.jobs.tick()
        assert h.jobs.job.state == "charging"
        h.change(available=False, present=None, scope=None, session=None, now=NOW.replace(hour=6, minute=1))
        await h.jobs.tick()
        h.change(now=NOW.replace(hour=6, minute=2))
        await h.jobs.tick()
        assert (h.jobs.job.state, h.jobs.job.reason) == ("suspended", "connection_lost")
        await h.weekly.tick()
        assert h.weekly.consent is not None
        later = timedelta(hours=1, minutes=3)
        h.change(available=True, present=True, scope="c" * 64, now=NOW + later,
                 session=mark(later, 42 if proven else 43))
        # The suspended order decides first; the plan waits for it.
        await h.weekly.tick()
        assert h.weekly.consent is not None
        await h.jobs.tick()
        await h.weekly.tick()
        if proven:
            assert h.jobs.job.state == "charging"
            assert h.weekly.consent.scope == "c" * 64
            assert h.weekly.reason == "active"
        else:
            assert h.jobs.job.state == "interrupted"
            assert h.weekly.consent is None
            assert h.weekly.reason == "session_ended"
    asyncio.run(run())
