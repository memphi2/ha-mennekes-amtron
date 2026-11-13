"""A stop is authority even while waiting behind a durable start transaction."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from tests.test_weekly_controller import SCOPE, Harness


@pytest.mark.parametrize("cancel", [False, True])
def test_stop_waiting_for_owner_lock_fences_start_before_it_can_execute(tmp_path, cancel):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        c = h.jobs
        await c._lock.acquire()
        task = asyncio.create_task(c.stop())
        try:
            await asyncio.sleep(0)
            assert not task.done()
            assert c.stop_requested()
            assert c.snapshot()["manual_stop_pending"]
            assert c.current_policy(f"service-job:{c.job.identifier}", True) is False
            assert c.current_policy(f"service-job:{c.job.identifier}", False) is True
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert c.stop_requested()
                assert c.snapshot()["manual_stop_pending"]
        finally:
            c._lock.release()
        if not cancel:
            result = await task
            assert result["manual_stop_pending"] is False
            assert c.job.state == "stopped"
        else:
            await c.stop()
            assert not c.snapshot()["manual_stop_pending"]
        await h.weekly.tick()
        assert h.weekly.consent is None
    asyncio.run(run())


def test_cancelled_stop_without_job_cannot_be_undone_by_new_preview(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.jobs._lock.acquire()
        task = asyncio.create_task(h.jobs.stop())
        try:
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            h.jobs._lock.release()
        assert h.jobs.current_policy(None, True) is False
        assert h.jobs.current_policy(None, False) is None
        with pytest.raises(JobRequestError, match="weekly_stop_unconfirmed"):
            h.preview()
        # A clear without a confirmed pause cannot remove the fence.
        with pytest.raises(JobRequestError, match="job_clear_requires_pause"):
            await h.jobs.clear(revision=h.jobs.revision)
        assert h.jobs.snapshot()["manual_stop_pending"]
        h.change(current_a=1.0)
        result = await h.jobs.clear(revision=h.jobs.revision)
        assert result["manual_stop_pending"] is False
        assert h.preview()["scope"] == SCOPE
    asyncio.run(run())


def test_manual_stop_during_successor_persistence_never_enables_current(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        await h.save()
        await h.activate()
        await h.complete()
        h.change(now=h.sample.now.replace(hour=8))
        before = len(h.calls)
        original = h.jobs._save
        started, resume = asyncio.Event(), asyncio.Event()

        async def save():
            started.set()
            await resume.wait()
            await original()

        monkeypatch.setattr(h.jobs, "_save", save)
        successor = asyncio.create_task(h.jobs.successor(
            {"id": "c" * 32, "start_at": h.sample.now.isoformat(),
             "finish_at": (h.sample.now + timedelta(hours=1)).isoformat(),
             "current_a": 12, "energy_kwh": 10},
            revision=h.jobs.revision, scope=SCOPE, predecessor=h.jobs.job.identifier,
            stop_epoch=h.jobs.manual_stop_epoch,
        ))
        await started.wait()
        stop = asyncio.create_task(h.jobs.stop())
        await asyncio.sleep(0)
        assert h.jobs.stop_requested()
        resume.set()
        await asyncio.gather(successor, stop)
        assert all(value is None for value, _owner, _deadline in h.calls[before:])
        assert h.jobs.job.state == "stopped"
        await h.weekly.tick()
        assert h.weekly.consent is None
    asyncio.run(run())
