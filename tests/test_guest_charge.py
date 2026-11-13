"""Guest orders use explicit energy/time bounds, never another vehicle's SoC."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import ChargeJob
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    ChargeJobController,
    JobInputs,
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_store import ChargeJobStore
from custom_components.mennekes_amtron_rtu.device.guest_charge import guest_request

NOW = datetime(2026, 11, 13, 18, tzinfo=UTC)
SCOPE = "a" * 64


def request(**values):
    return {"id": "b" * 32, "duration_minutes": 60, "current_a": 12.0, "energy_kwh": 15.0, **values}


class Harness:
    def __init__(self, path):
        self.sample = JobInputs(NOW, True, True, SCOPE, 100.0, True, True, False, 8.0, 16.0)
        self.calls = []
        self.store = ChargeJobStore(path / "charge-job.json", "fixture")
        self.controller = ChargeJobController(self.store, lambda: self.sample, self.apply)

    def change(self, **values):
        self.sample = replace(self.sample, **values)

    async def apply(self, value, _owner, _deadline):
        self.calls.append(value)
        self.change(current_a=1 if value is None else value)

    def preview(self, **values):
        return self.controller.preview_guest(request(**values), revision=self.controller.revision, scope=SCOPE)

    async def confirm(self, preview):
        return await self.controller.create(preview["request"], revision=preview["revision"], scope=preview["scope"])


@pytest.mark.parametrize("minutes", [1, 30, 60, 1440])
def test_guest_is_an_absolute_immediate_order(minutes):
    raw = guest_request(request(duration_minutes=minutes), now=NOW)
    job = ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW)
    assert job.kind == "guest"
    assert job.start_at == NOW
    assert job.finish_at == NOW + timedelta(minutes=minutes)
    assert job.energy.target_kwh == 15
    assert job.energy.baseline_kwh == 100
    assert ChargeJob.restore(job.snapshot(), restarted=False).snapshot() == job.snapshot()
    assert ChargeJob.restore(job.snapshot()).reason == "service_restarted"


@pytest.mark.parametrize("minutes", [None, True, False, 0, -1, 1441, 1.0, 1.5, "30", [], float("nan")])
def test_invalid_duration_never_becomes_a_timer(minutes):
    with pytest.raises(ValueError, match="guest duration"):
        guest_request(request(duration_minutes=minutes), now=NOW)


@pytest.mark.parametrize("raw", [None, [], {}, {"duration_minutes": 30}, {**request(), "soc": 80}])
def test_guest_input_does_not_accept_vehicle_or_other_unknown_fields(raw):
    with pytest.raises(ValueError, match="guest request fields"):
        guest_request(raw, now=NOW)


@pytest.mark.parametrize("kind", [True, None, [], "vehicle", "", "GUEST"])
def test_unknown_kind_is_rejected_on_create_and_restore(kind):
    raw = guest_request(request(), now=NOW)
    saved = ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW).snapshot()
    with pytest.raises(ValueError, match="job kind"):
        ChargeJob.create({**raw, "kind": kind}, binding=SCOPE, total_kwh=100, now=NOW)
    with pytest.raises(ValueError, match="job kind"):
        ChargeJob.restore({**saved, "kind": kind})


@pytest.mark.parametrize("case", ["future", "long", "expired_preview", "boost"])
def test_guest_limits_cannot_be_bypassed_with_absolute_payloads(case):
    raw = guest_request(request(), now=NOW)
    if case == "future":
        raw["start_at"] = (NOW + timedelta(minutes=1)).isoformat()
    elif case == "long":
        raw["finish_at"] = (NOW + timedelta(days=2)).isoformat()
    elif case == "expired_preview":
        with pytest.raises(ValueError, match="job window"):
            ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW + timedelta(seconds=61))
        return
    else:
        job = ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW)
        with pytest.raises(ValueError, match="boost must end"):
            job.set_boost(NOW.isoformat(), now=NOW - timedelta(seconds=1))
        with pytest.raises(ValueError, match="invalid saved boost"):
            ChargeJob.restore({**job.snapshot(), "boost_until": NOW.isoformat()})
        return
    with pytest.raises(ValueError, match="guest window"):
        ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW)


def test_old_scheduled_format_is_unchanged_and_guest_duration_survives_storage(tmp_path):
    raw = guest_request(request(), now=NOW)
    guest = ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW)
    store = ChargeJobStore(tmp_path / "charge-job.json", "fixture")
    store.save(guest)
    loaded = store.load()
    assert loaded.kind == "guest"
    assert loaded.terminal
    raw.pop("kind")
    scheduled = ChargeJob.create(raw, binding=SCOPE, total_kwh=100, now=NOW)
    assert "kind" not in scheduled.snapshot()
    assert ChargeJob.restore(scheduled.snapshot(), restarted=False).kind == "scheduled"
    with pytest.raises(ValueError, match="saved guest window"):
        ChargeJob.restore({**guest.snapshot(), "finish_at": (NOW + timedelta(days=2)).isoformat()})


@pytest.mark.parametrize("when", ["preview", "confirm", "running"])
def test_vehicle_target_conflict_is_rechecked_before_writing(tmp_path, when):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        if when == "preview":
            h.change(vehicle_target_active=True)
            with pytest.raises(JobRequestError, match="guest_vehicle_target_active"):
                h.preview()
            assert h.calls == []
            return
        preview = h.preview()
        if when == "confirm":
            h.change(vehicle_target_active=True)
            with pytest.raises(JobRequestError, match="guest_vehicle_target_active"):
                await h.confirm(preview)
            assert h.calls == []
            return
        await h.confirm(preview)
        h.change(vehicle_target_active=True)
        assert c.stop_requested()
        assert c.current_policy("service-job:" + "b" * 32, True) is False
        await c.tick()
        assert c.job.reason == "independent_stop"
        assert h.calls[-1] is None
        h.change(vehicle_target_active=False)
        await c.tick()
        assert c.job.terminal
        assert c.snapshot()["vehicle_target_active"] is False
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": False}, {"scope": None},
    {"meter_fresh": False}, {"permitted": False}, {"independent_stop": True}, {"pending": True},
])
def test_guest_does_not_bypass_existing_admission_guards(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        h.change(**change)
        with pytest.raises(JobRequestError, match="job_not_permitted"):
            h.preview()
        assert h.calls == []
        assert not h.store.path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("finish", ["energy", "deadline", "manual", "unplug"])
def test_guest_completion_retries_and_restart_never_extend_permission(tmp_path, finish):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        preview = h.preview()
        assert not h.calls
        assert not h.store.path.exists()
        await h.confirm(preview)
        assert c.job.kind == "guest"
        assert h.calls == [12]
        assert c.snapshot()["boost_available"] is False
        h.change(total_kwh=103, now=NOW + timedelta(minutes=5))
        await h.confirm(preview)
        assert c.job.energy.baseline_kwh == 100
        assert c.job.finish_at == NOW + timedelta(hours=1)
        if finish == "energy":
            h.change(total_kwh=115)
        elif finish == "deadline":
            h.change(now=NOW + timedelta(hours=1))
        elif finish == "manual":
            await c.stop()
        else:
            h.change(present=False, scope=None)
        await c.tick()
        assert c.job.terminal
        assert c.snapshot()["job"]["kind"] == "guest"
        before = list(h.calls)
        await h.confirm(preview)
        assert h.calls == before
        restored = ChargeJobController(h.store, lambda: h.sample, h.apply)
        await restored.initialize()
        assert restored.job.terminal
        assert restored.job.kind == "guest"
    asyncio.run(run())
