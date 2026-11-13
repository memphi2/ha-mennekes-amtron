"""A temporary early start never replaces the original plan or its stops."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import ChargeJob
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from tests.test_service_charge_controller import NOW, SCOPE, Harness


async def scheduled(path):
    h = Harness(path)
    await h.controller.initialize()
    await h.create(delay=3 * 3600)
    return h


@pytest.mark.parametrize("minutes", [1, 30, 120])
def test_boost_returns_to_the_original_plan_and_keeps_the_energy_baseline(tmp_path, minutes):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        before = c.job.snapshot()
        writes = list(h.calls)
        preview = c.preview_boost(minutes, revision=c.revision, scope=SCOPE)
        assert h.calls == writes
        assert c.job.snapshot() == before
        await c.boost(preview["until"], revision=preview["revision"], scope=SCOPE)
        assert c.job.state == "charging"
        assert h.calls[-1][0] == 12
        assert c.job.start_at.isoformat() == before["start_at"]
        assert c.job.energy.baseline_kwh == 100
        assert c.job.energy.target_kwh == 10
        assert c.snapshot()["job"]["boost_until"] == preview["until"]
        restored = h.store.load()
        assert restored.state == "interrupted"
        assert restored.boost_until is None
        h.change(now=NOW + timedelta(minutes=minutes), total_kwh=102)
        # The pre-write fence works before the scheduler catches up.
        assert c.stop_requested()
        assert c.current_policy("service-job:" + "b" * 32, True) is False
        await c.tick()
        assert c.job.state == "scheduled"
        assert c.job.boost_until is None
        assert h.calls[-1][0] is None
        h.change(now=NOW + timedelta(hours=3, seconds=1))
        await c.tick()
        assert c.job.state == "charging"
        assert h.calls[-1][0] == 12
        h.change(total_kwh=110)
        await c.tick()
        assert c.job.state == "reached"
        assert h.calls[-1][0] is None
    asyncio.run(run())


def test_boost_is_clipped_at_the_original_start_without_a_pause_pulse(tmp_path):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        h.change(now=NOW + timedelta(hours=2, minutes=50))
        await c.tick()
        preview = c.preview_boost(30, revision=c.revision, scope=SCOPE)
        assert preview["until"] == c.job.start_at.isoformat()
        await c.boost(preview["until"], revision=preview["revision"], scope=SCOPE)
        before = list(h.calls)
        h.change(now=c.job.start_at)
        assert not c.stop_requested()
        await c.tick()
        assert h.calls == before
        assert c.job.state == "charging"
        assert c.job.boost_until is None
    asyncio.run(run())


@pytest.mark.parametrize("minutes", [None, True, False, -1, 0, 121, 1.5, 30.0, "30", {}, []])
def test_invalid_duration_is_never_a_permission(tmp_path, minutes):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        before = c.job.snapshot()
        with pytest.raises(JobRequestError, match="invalid_boost_duration"):
            c.preview_boost(minutes, revision=c.revision, scope=SCOPE)
        assert c.job.snapshot() == before
    asyncio.run(run())


@pytest.mark.parametrize("until", [
    None, True, "invalid", "2026-11-13T18:30:00",
    NOW.isoformat(), (NOW - timedelta(seconds=1)).isoformat(),
    (NOW + timedelta(minutes=121)).isoformat(),
    (NOW + timedelta(hours=4)).isoformat(),
])
def test_invalid_absolute_confirmation_cannot_slide_the_timer(tmp_path, until):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        before = list(h.calls)
        with pytest.raises(ValueError, match=r"boost|time"):
            await c.boost(until, revision=c.revision, scope=SCOPE)
        assert c.job.boost_until is None
        assert h.calls == before
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": None}, {"present": False},
    {"scope": "c" * 64}, {"meter_fresh": False}, {"permitted": False},
    {"independent_stop": True}, {"pending": True},
    {"now": NOW + timedelta(hours=3)},
])
def test_boost_needs_current_permission_and_the_same_connection(tmp_path, change):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        h.change(**change)
        assert not c.snapshot()["boost_available"]
        with pytest.raises(JobRequestError, match="job_boost_unavailable"):
            c.preview_boost(30, revision=c.revision, scope=SCOPE)
        assert c.job.boost_until is None
    asyncio.run(run())


@pytest.mark.parametrize("condition", ["not_ready", "closing", "storage", "no_job", "terminal", "scope_ended"])
def test_owner_state_does_not_allow_a_boost(tmp_path, condition):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        if condition == "not_ready":
            c.ready = False
        elif condition == "closing":
            c._closing = True
        elif condition == "storage":
            c.storage_error = True
        elif condition == "no_job":
            c.job = None
        elif condition == "scope_ended":
            c.job.scope_ended = True
        else:
            c.job.stop()
        assert not c.snapshot()["boost_available"]
        with pytest.raises(JobRequestError, match="job_boost_unavailable"):
            c.preview_boost(30, revision=c.revision, scope=SCOPE)
    asyncio.run(run())


def test_stale_or_wrong_scope_confirmation_is_rejected(tmp_path):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        with pytest.raises(JobRequestError, match="job_boost_unavailable"):
            c.preview_boost(30, revision=c.revision, scope="old")
        preview = c.preview_boost(30, revision=c.revision, scope=SCOPE)
        await c.stop()
        with pytest.raises(JobRequestError, match="job_changed"):
            await c.boost(preview["until"], revision=preview["revision"], scope=SCOPE)
        with pytest.raises(ValueError, match="boost must end"):
            c.job.set_boost(preview["until"], now=NOW)
        assert c.job.state == "stopped"
    asyncio.run(run())


def test_cancel_boost_pauses_but_keeps_the_original_order(tmp_path):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        await c.boost((NOW + timedelta(minutes=30)).isoformat(), revision=c.revision, scope=SCOPE)
        await c.cancel_boost(revision=c.revision)
        assert c.job.state == "scheduled"
        assert c.job.boost_until is None
        assert h.calls[-1][0] is None
        before = list(h.calls)
        await c.cancel_boost(revision=c.revision)
        assert h.calls == before
        await c.stop()
        await c.cancel_boost(revision=c.revision)
        assert c.job.state == "stopped"
        await c.clear(revision=c.revision)
        with pytest.raises(JobRequestError, match="job_boost_unavailable"):
            await c.cancel_boost(revision=c.revision)
    asyncio.run(run())


@pytest.mark.parametrize("ending", ["manual", "energy", "connection", "source", "clock", "meter_reset", "independent", "unplug"])
def test_all_terminal_stops_cancel_boost_and_never_return_to_the_plan(tmp_path, ending):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        await c.boost((NOW + timedelta(minutes=30)).isoformat(), revision=c.revision, scope=SCOPE)
        if ending == "manual":
            await c.stop()
        else:
            changes = {
                "energy": {"total_kwh": 110}, "connection": {"available": False},
                "source": {"scope": "c" * 64}, "clock": {"now": NOW - timedelta(seconds=10)},
                "meter_reset": {"total_kwh": 90}, "independent": {"independent_stop": True},
                "unplug": {"present": False},
            }
            h.change(**changes[ending])
            await c.tick()
        assert c.job.terminal
        assert c.job.boost_until is None
        h.change(now=NOW + timedelta(hours=3), available=True, present=True, independent_stop=False)
        await c.tick()
        assert c.job.terminal
    asyncio.run(run())


def test_meter_gap_and_expiry_never_extend_boost(tmp_path):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        end = (NOW + timedelta(minutes=30)).isoformat()
        await c.boost(end, revision=c.revision, scope=SCOPE)
        h.change(meter_fresh=False)
        await c.tick()
        assert c.job.state == "waiting_meter"
        assert h.calls[-1][0] is None
        h.change(meter_fresh=True)
        await c.tick()
        assert c.job.state == "charging"
        assert c.job.boost_until.isoformat() == end
        h.change(now=NOW + timedelta(minutes=31))
        await c.tick()
        assert c.job.state == "scheduled"
    asyncio.run(run())


def test_failed_boost_save_latches_a_stop_instead_of_starting(tmp_path, monkeypatch):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        before = list(h.calls)
        monkeypatch.setattr(h.store, "save", Mock(side_effect=OSError("storage failure")))
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await c.boost((NOW + timedelta(minutes=30)).isoformat(), revision=c.revision, scope=SCOPE)
        assert c.job.state == "interrupted"
        assert c.job.boost_until is None
        assert h.calls == before  # Already paused; do not duplicate the write.
        assert c.stop_requested()
    asyncio.run(run())


def test_model_roundtrip_preserves_boost_only_without_a_restart(tmp_path):
    async def run():
        h = await scheduled(tmp_path)
        c = h.controller
        old = c.job.snapshot()
        assert "boost_until" not in old
        assert ChargeJob.restore(old, restarted=False).boost_until is None
        await c.boost((NOW + timedelta(minutes=30)).isoformat(), revision=c.revision, scope=SCOPE)
        raw = c.job.snapshot()
        assert ChargeJob.restore(raw, restarted=False).snapshot() == raw
        restored = ChargeJob.restore(raw)
        assert restored.state == "interrupted"
        assert restored.boost_until is None
        assert not restored.allows_at(NOW)
        for value in (None, (NOW + timedelta(hours=4)).isoformat()):
            with pytest.raises(ValueError, match=r"boost|time"):
                ChargeJob.restore({**raw, "boost_until": value})
        await c.stop()
        with pytest.raises(ValueError, match="invalid saved boost"):
            ChargeJob.restore({**c.job.snapshot(), "boost_until": raw["boost_until"]})
    asyncio.run(run())
