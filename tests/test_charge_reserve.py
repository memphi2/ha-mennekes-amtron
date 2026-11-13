"""A minimum early AC-energy portion shares the original cap and stop rules."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import ChargeJob
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    ChargeJobController,
    JobInputs,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_store import ChargeJobStore
from custom_components.mennekes_amtron_rtu.device.charge_reserve import ChargeReserve
from custom_components.mennekes_amtron_rtu.device.weekly_plan import WeeklyPlan

NOW = datetime(2026, 11, 16, 0, tzinfo=UTC)
SCOPE = "a" * 64


def request(**changes):
    return {"id": "b" * 32, "start_at": (NOW + timedelta(hours=4)).isoformat(),
            "finish_at": (NOW + timedelta(hours=8)).isoformat(), "energy_kwh": 20,
            "current_a": 12, "reserve_kwh": 5, **changes}


def model(**changes):
    return ChargeJob.create(request(**changes), binding=SCOPE, total_kwh=100, now=NOW)


def observe(job, total=100, **changes):
    job.observe(**{"now": NOW + timedelta(minutes=1), "available": True,
                   "present": True, "matches": True, "total_kwh": total,
                   "meter_fresh": True, **changes})


def test_reserve_charges_now_then_waits_and_finishes_under_one_ceiling():
    job = model()
    observe(job)
    assert job.state == "charging"
    assert job.reserve.reached is False
    assert not job.allows_at(NOW)
    assert job.allows_at(NOW, total_kwh=100)
    observe(job, 105)
    assert job.state == "scheduled"
    assert job.reserve.reached
    assert job.energy.baseline_kwh == 100
    assert job.energy.target_kwh == 20
    observe(job, 105, now=NOW + timedelta(hours=4))
    assert job.state == "charging"
    observe(job, 120, now=NOW + timedelta(hours=5))
    assert job.state == "reached"
    assert not job.allows_at(NOW + timedelta(hours=5), total_kwh=120)


@pytest.mark.parametrize("value", [None, True, "5", -1, 0.05, 0.11, 20.1, float("nan"), float("inf"), 10**1000])
def test_bad_or_excessive_reserve_is_rejected_before_a_job_exists(value):
    with pytest.raises(ValueError, match=r"energy|reserve"):
        model(reserve_kwh=value)


@pytest.mark.parametrize("value", [0, 0.1, 5.5, 20])
def test_reserve_uses_existing_energy_bounds_and_zero_is_opt_out(value):
    job = model(reserve_kwh=value)
    observe(job)
    assert job.state == ("charging" if value else "scheduled")
    if value:
        observe(job, 100 + value)
        assert job.reserve.reached
        assert job.state == ("reached" if value == 20 else "scheduled")
    else:
        assert job.reserve is None
        assert "reserve" not in job.snapshot()


def test_guest_jobs_cannot_acquire_an_early_reserve_phase():
    with pytest.raises(ValueError, match="scheduled jobs"):
        model(kind="guest", start_at=NOW.isoformat())


@pytest.mark.parametrize("total", [None, True, "100", -1, float("nan"), float("inf"), 10**1000])
def test_missing_or_invalid_meter_never_grants_or_completes_reserve(total):
    job = model()
    assert not job.allows_at(NOW, total_kwh=total)
    observe(job, total)
    assert job.state == "waiting_meter"
    assert not job.reserve.reached
    observe(job, 102)
    assert job.state == "charging"


def test_stale_meter_does_not_count_and_falling_meter_never_rearms():
    job = model()
    observe(job, 106, meter_fresh=False)
    assert not job.reserve.reached
    observe(job, 105)
    assert job.reserve.reached
    observe(job, 104)
    assert job.state == "interrupted"
    assert job.reason == "meter_reset"
    assert job.reserve.reached


def test_reserve_cannot_be_smaller_than_the_meters_representable_resolution():
    with pytest.raises(ValueError, match="represent"):
        ChargeJob.create(request(reserve_kwh=0.1), binding=SCOPE, total_kwh=1e16, now=NOW)


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": False}, {"matches": False},
    {"independent_stop": True}, {"now": NOW - timedelta(seconds=6)},
])
def test_reserve_never_overrides_an_independent_stop(change):
    job = model()
    observe(job, **change)
    assert job.terminal
    observe(job)
    assert job.terminal


def test_explicit_boost_after_reserve_does_not_reset_either_meter_baseline():
    job = model()
    observe(job, 105)
    job.set_boost((NOW + timedelta(minutes=30)).isoformat(), now=NOW + timedelta(minutes=1))
    observe(job, 105, now=NOW + timedelta(minutes=2))
    assert job.state == "charging"
    assert job.reserve.reached
    job.cancel_boost()
    observe(job, 106, now=NOW + timedelta(minutes=3))
    assert job.state == "scheduled"
    assert job.energy.baseline_kwh == 100


@pytest.mark.parametrize("reached", [False, True])
def test_atomic_snapshot_retains_latch_but_restart_does_not_restore_permission(reached):
    job = model()
    observe(job, 105 if reached else 100)
    raw = job.snapshot()
    same = ChargeJob.restore(deepcopy(raw), restarted=False)
    assert same.snapshot() == raw
    restarted = ChargeJob.restore(raw)
    assert restarted.state == "interrupted"
    assert restarted.reserve.reached is reached
    assert not restarted.allows_at(NOW, total_kwh=100)


@pytest.mark.parametrize("raw", [None, {}, {"energy_kwh": 5, "reached": 1},
                                  {"energy_kwh": 0, "reached": False},
                                  {"energy_kwh": 21, "reached": False},
                                  {"energy_kwh": 5, "reached": False, "resume": True}])
def test_invalid_saved_reserve_is_not_treated_as_disabled(raw):
    value = model().snapshot()
    value["reserve"] = raw
    with pytest.raises(ValueError, match="reserve"):
        ChargeJob.restore(value)


def test_a_completed_total_goal_requires_a_completed_reserve_latch():
    job = model()
    observe(job, 120)
    raw = job.snapshot()
    raw["reserve"]["reached"] = False
    with pytest.raises(ValueError, match="inconsistent"):
        ChargeJob.restore(raw)
    assert not ChargeReserve(5).needed(100, None)


class Harness:
    def __init__(self, path):
        self.sample = JobInputs(NOW, True, True, SCOPE, 100.0, True, True, False, 8.0, 16.0)
        self.calls = []
        self.store = ChargeJobStore(path / "charge-job.json", "fixture")
        self.jobs = ChargeJobController(self.store, lambda: self.sample, self.apply)

    def change(self, **changes):
        self.sample = replace(self.sample, **changes)

    async def apply(self, value, _owner, _deadline):
        self.calls.append(value)
        self.change(current_a=1.0 if value is None else value)

    async def create(self, **changes):
        await self.jobs.initialize()
        p = self.jobs.preview(request(**changes), revision=self.jobs.revision, scope=SCOPE)
        assert self.calls == []
        return await self.jobs.create(p["request"], revision=p["revision"], scope=SCOPE)


def test_controller_reserve_transition_prewrite_guard_and_idempotent_confirmation(tmp_path):
    async def run():
        h = Harness(tmp_path)
        result = await h.create()
        assert result["job"]["reserve_kwh"] == 5
        assert result["job"]["reserve_reached"] is False
        assert h.calls == [12]
        assert not h.jobs.stop_requested()
        h.change(total_kwh=105)
        assert h.jobs.stop_requested()
        assert h.jobs.current_policy("service-job:" + "b" * 32, True) is False
        await h.jobs.tick()
        assert h.calls == [12, None]
        assert h.jobs.snapshot()["job"]["reserve_reached"] is True
        assert h.store.load().reserve.reached
        await h.jobs.create(request(), revision="old", scope=SCOPE)
        assert h.calls == [12, None]
        assert h.jobs.job.energy.baseline_kwh == 100
        h.change(now=NOW + timedelta(hours=4))
        await h.jobs.tick()
        assert h.calls[-1] == 12
        await h.jobs.stop()
        h.change(now=NOW + timedelta(hours=5))
        await h.jobs.tick()
        assert h.calls[-1] is None
    asyncio.run(run())


def test_failed_reserve_completion_save_still_requests_pause(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.create()
        h.change(total_kwh=105)
        monkeypatch.setattr(h.store, "save", Mock(side_effect=OSError("fixture")))
        await h.jobs.tick()
        assert h.jobs.storage_error
        assert h.jobs.job.reason == "storage_error"
        assert h.calls[-1] is None
    asyncio.run(run())


def test_legacy_job_output_and_storage_shape_are_unchanged(tmp_path):
    async def run():
        h = Harness(tmp_path)
        result = await h.create(reserve_kwh=0)
        assert "reserve_kwh" not in result["job"]
        assert "reserve_reached" not in result["job"]
        assert "reserve" not in h.jobs.job.snapshot()
        assert h.calls == [None]
    asyncio.run(run())


@pytest.mark.parametrize("value", [0, 0.1, 5, 20])
def test_weekly_recipes_carry_exact_reserve_into_each_absolute_order(value):
    plan = WeeklyPlan.parse({"id": "c" * 32, "vehicle_profile": "fixture", "timezone": "UTC",
                             "weekdays": list(range(7)), "departure_time": "08:00", "charge_minutes": 120,
                             "current_a": 12, "energy_kwh": 20, "reserve_kwh": value})
    canonical = plan.snapshot()
    assert canonical.get("reserve_kwh", 0) == value
    assert WeeklyPlan.parse(canonical).snapshot() == canonical
    first, second, *_ = plan.upcoming(now=NOW)
    assert first.snapshot().get("reserve_kwh", 0) == value
    for row in (first, second):
        proposal = row.job_request(now=NOW, vehicle_profile="fixture")
        assert proposal.get("reserve_kwh", 0) == value
        accepted = ChargeJob.create(proposal, binding=SCOPE, total_kwh=100, now=NOW)
        assert (accepted.reserve.target_kwh if accepted.reserve else 0) == value
    assert first.identifier != second.identifier


def test_weekly_reserve_above_total_ceiling_is_rejected():
    with pytest.raises(ValueError, match="reserve exceeds"):
        WeeklyPlan.parse({"id": "c" * 32, "vehicle_profile": "fixture", "timezone": "UTC",
                          "weekdays": [0], "departure_time": "08:00", "charge_minutes": 120,
                          "current_a": 12, "energy_kwh": 20, "reserve_kwh": 21})
