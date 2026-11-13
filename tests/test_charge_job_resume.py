"""An order continues after the app was away only on proof of the same plug-in."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import (
    ChargeJob,
    SessionMark,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    ChargeJobController,
    JobInputs,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_store import ChargeJobStore

NOW = datetime(2026, 11, 16, 6, tzinfo=UTC)
SCOPE, OTHER, CONTEXT = "a" * 64, "c" * 64, "e" * 64


def mark(after=timedelta(0), *, duration=None, energy=2.0, count=42, context=CONTEXT):
    """The wallbox's session record ``after`` the reference reading."""
    seconds = 600 + int(after.total_seconds()) if duration is None else duration
    return SessionMark(NOW + after, seconds, energy, count, context)


def model(**changes):
    request = {"id": "b" * 32, "start_at": NOW.isoformat(),
               "finish_at": (NOW + timedelta(hours=4)).isoformat(),
               "current_a": 12, "energy_kwh": 10, **changes}
    job = ChargeJob.create(request, binding=SCOPE, total_kwh=100, now=NOW)
    job.session = mark()
    return job


def observe(job, after=timedelta(minutes=5), **changes):
    values = {"now": NOW + after, "available": True, "present": True, "matches": False,
              "total_kwh": 100, "meter_fresh": True, "scope": OTHER, "session": mark(after), **changes}
    job.observe(**values)


def restarted(job=None):
    return ChargeJob.restore((job or model()).snapshot())


@pytest.mark.parametrize(("later", "same"), [
    (mark(timedelta(minutes=5)), True),
    (mark(timedelta(minutes=5), duration=600 + 300 + 120), True),
    (mark(timedelta(minutes=5), duration=600 + 300 - 121), False),
    # Unplugged and plugged in again: the session clock starts over.
    (mark(timedelta(minutes=5), duration=30), False),
    (mark(timedelta(minutes=5), count=43), False),
    (mark(timedelta(minutes=5), energy=0.0), False),
    (mark(timedelta(minutes=5), energy=1.96), True),
    (mark(timedelta(minutes=5), context="f" * 64), False),
    # A wall clock that jumped back while the session clock ran on.
    (mark(timedelta(minutes=-30), duration=900), False),
])
def test_only_a_continuing_session_clock_and_counter_prove_the_same_plug_in(later, same):
    assert mark().continues(later) is same


@pytest.mark.parametrize("raw", [
    None, {}, {**mark().snapshot(), "vin": "x"},
    {**mark().snapshot(), "duration_s": -1}, {**mark().snapshot(), "duration_s": 1.5},
    {**mark().snapshot(), "duration_s": True}, {**mark().snapshot(), "count": 2**32},
    {**mark().snapshot(), "energy_kwh": float("nan")}, {**mark().snapshot(), "energy_kwh": -1},
    {**mark().snapshot(), "energy_kwh": True}, {**mark().snapshot(), "context": "x"},
    {**mark().snapshot(), "at": "2026-11-16T06:00:00"},
])
def test_corrupt_saved_session_is_rejected(raw):
    with pytest.raises(ValueError, match=r"session|job time"):
        SessionMark.restore(raw)
    if isinstance(raw, dict) and raw.keys() == mark().snapshot().keys():
        saved = model().snapshot()
        saved["session"] = raw
        with pytest.raises(ValueError, match=r"session|job time"):
            ChargeJob.restore(saved)


def test_a_restart_suspends_and_a_proven_plug_in_continues_on_the_new_connection():
    job = model(reserve_kwh=2)
    job.observe(now=NOW, available=True, present=True, matches=True, total_kwh=101, meter_fresh=True)
    assert job.state == "charging"
    job = restarted(job)
    assert (job.state, job.reason) == ("suspended", "service_restarted")
    assert job.stop_requested
    assert not job.allows_at(NOW + timedelta(minutes=5), total_kwh=101)
    with pytest.raises(ValueError, match="boost"):
        job.set_boost((NOW + timedelta(minutes=10)).isoformat(), now=NOW)
    observe(job, total_kwh=101)
    assert (job.state, job.reason) == ("charging", "charging")
    assert job.binding == OTHER
    # The ceiling, its baseline and the reserve continue unchanged.
    assert job.energy.baseline_kwh == 100
    assert job.reserve.reached is False
    observe(job, after=timedelta(minutes=6), total_kwh=110, matches=True)
    assert job.state == "reached"


@pytest.mark.parametrize(("changes", "scope_ended"), [
    ({"session": mark(timedelta(minutes=5), duration=30)}, False),
    ({"session": mark(timedelta(minutes=5), count=43)}, False),
    ({"present": False, "scope": None, "session": None}, True),
    ({"now": NOW + timedelta(hours=4)}, False),
])
def test_a_failed_proof_unplug_or_ended_window_interrupts_the_suspended_order(changes, scope_ended):
    job = restarted()
    observe(job, **changes)
    assert (job.state, job.reason) == ("interrupted", "service_restarted")
    assert job.scope_ended is scope_ended
    assert job.binding == SCOPE
    observe(job)
    assert job.state == "interrupted"


def test_missing_evidence_interrupts_within_a_minute_of_a_readable_wallbox():
    job = restarted()
    # While the wallbox is unreadable nobody else can charge either: wait.
    observe(job, available=False, present=None, scope=None, session=None)
    observe(job, after=timedelta(hours=1), available=False, present=None, scope=None, session=None)
    assert job.state == "suspended"
    observe(job, after=timedelta(hours=1, minutes=1), session=None)
    observe(job, after=timedelta(hours=1, minutes=1, seconds=59), present=None, session=None)
    assert job.state == "suspended"
    observe(job, after=timedelta(hours=1, minutes=2), session=None)
    assert (job.state, job.reason) == ("interrupted", "service_restarted")


def test_a_clock_moved_backwards_during_the_suspension_interrupts_it():
    job = restarted()
    observe(job, session=None)
    observe(job, after=timedelta(minutes=4), session=None)
    assert job.state == "interrupted"


def test_a_lasting_gap_suspends_and_the_unchanged_scope_needs_no_proof():
    job = model()
    job.observe(now=NOW, available=True, present=True, matches=True, total_kwh=100, meter_fresh=True)
    observe(job, after=timedelta(seconds=1), available=False, present=None)
    observe(job, after=timedelta(seconds=61), available=False, present=None)
    assert (job.state, job.reason) == ("suspended", "connection_lost")
    observe(job, after=timedelta(seconds=70), scope=SCOPE, session=None, matches=True)
    assert (job.state, job.binding) == ("charging", SCOPE)


def test_without_a_session_record_a_restart_or_gap_still_interrupts():
    job = model()
    job.session = None
    assert restarted(job).state == "interrupted"
    job.observe(now=NOW, available=False, present=None, matches=True, total_kwh=100, meter_fresh=True)
    job.observe(now=NOW + timedelta(seconds=60), available=False, present=None, matches=True, total_kwh=100, meter_fresh=True)
    assert (job.state, job.reason) == ("interrupted", "connection_lost")


@pytest.mark.parametrize("change", [
    {"reason": "vehicle_changed"}, {"session": None}, {"boost_until": NOW.isoformat()},
    {"scope_ended": True}, {"energy": "reached"},
])
def test_inconsistent_saved_suspension_is_rejected(change):
    raw = restarted().snapshot()
    if change.get("energy") == "reached":
        raw["energy"] = model().snapshot()["energy"] | {"status": "reached"}
    elif "session" in change:
        raw.pop("session")
    else:
        raw.update(change)
    with pytest.raises(ValueError, match="saved"):
        ChargeJob.restore(raw)
    with pytest.raises(ValueError, match="suspension"):
        model().suspend("vehicle_changed")


class Harness:
    def __init__(self, path):
        self.sample = JobInputs(NOW, True, True, SCOPE, 100.0, True, True, False, 1.0, 16.0, session=mark())
        self.calls = []
        self.store = ChargeJobStore(path / "charge-job.json", "fixture")
        self.jobs = ChargeJobController(self.store, lambda: self.sample, self.apply)

    def change(self, **changes):
        self.sample = replace(self.sample, **changes)

    async def apply(self, value, _owner, _deadline):
        self.calls.append(value)
        self.change(current_a=1.0 if value is None else value)

    async def create(self):
        await self.jobs.initialize()
        request = {"id": "b" * 32, "start_at": NOW.isoformat(),
                   "finish_at": (NOW + timedelta(hours=4)).isoformat(), "current_a": 12, "energy_kwh": 10}
        p = self.jobs.preview(request, revision=self.jobs.revision, scope=SCOPE)
        await self.jobs.create(p["request"], revision=p["revision"], scope=SCOPE)


def test_session_progress_stays_in_ram_and_is_saved_with_each_decision(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.create()
        assert h.store.load().session == mark()
        before, revision = h.store.path.stat().st_mtime_ns, h.jobs.revision
        h.change(now=NOW + timedelta(minutes=3), session=mark(timedelta(minutes=3)))
        await h.jobs.tick()
        assert h.jobs.job.session == mark(timedelta(minutes=3))
        assert h.store.path.stat().st_mtime_ns == before
        assert h.jobs.revision == revision
        assert not h.jobs.stop_requested()
        await h.jobs.shutdown()
        saved = h.store.load()
        assert saved.session == mark(timedelta(minutes=3))
        assert (saved.state, saved.reason) == ("suspended", "service_restarted")
    asyncio.run(run())


@pytest.mark.parametrize("proven", [True, False])
def test_an_order_survives_a_restart_only_on_proof_and_otherwise_hands_back(tmp_path, proven):
    async def run():
        h = Harness(tmp_path)
        await h.create()
        assert h.calls == [12]
        h.change(now=NOW + timedelta(minutes=1), session=mark(timedelta(minutes=1)))
        await h.jobs.shutdown()
        assert h.calls[-1] is None
        after = Harness(tmp_path)
        after.change(now=NOW + timedelta(minutes=3), scope=OTHER,
                     session=mark(timedelta(minutes=3), count=42 if proven else 43))
        # Before the proof the restored order holds its pause.
        after.change(available=False, present=None)
        await after.jobs.initialize()
        assert after.jobs.job.state == "suspended"
        assert after.jobs.holding
        assert after.jobs.current_policy("ha-solar", True) is False
        await after.jobs.tick()
        assert after.calls == []
        after.change(available=True, present=True)
        await after.jobs.tick()
        if proven:
            assert after.jobs.job.state == "charging"
            assert after.calls == [12]
            assert after.store.load().binding == OTHER
        else:
            assert (after.jobs.job.state, after.jobs.job.reason) == ("interrupted", "service_restarted")
            assert not after.jobs.holding
            assert after.store.load() is None
            assert after.calls == []
    asyncio.run(run())


def test_a_closing_service_never_continues_its_suspended_order(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.create()
        h.jobs.job.suspend("connection_lost")
        await h.jobs.shutdown()
        assert h.jobs.job.state == "suspended"
        assert h.calls[-1] is None
    asyncio.run(run())


def test_a_suspended_order_offers_no_boost_even_on_the_same_connection(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        request = {"id": "b" * 32, "start_at": (NOW + timedelta(hours=2)).isoformat(),
                   "finish_at": (NOW + timedelta(hours=4)).isoformat(), "current_a": 12, "energy_kwh": 10}
        p = h.jobs.preview(request, revision=h.jobs.revision, scope=SCOPE)
        await h.jobs.create(p["request"], revision=p["revision"], scope=SCOPE)
        assert h.jobs.snapshot()["boost_available"]
        h.jobs.job.suspend("connection_lost")
        assert not h.jobs.snapshot()["boost_available"]
        h.jobs.observe()
        assert h.jobs.job.state == "scheduled"
        assert h.jobs.snapshot()["boost_available"]
    asyncio.run(run())
