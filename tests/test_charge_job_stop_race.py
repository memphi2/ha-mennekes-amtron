"""A stop is authority even while waiting behind a durable start transaction."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from tests.test_service_orders import NOW, SCOPE, Harness

REQUEST = {
    "id": "c" * 32, "start_at": NOW.isoformat(),
    "finish_at": (NOW + timedelta(hours=2)).isoformat(), "current_a": 10, "energy_kwh": 5,
}


async def _harness(tmp_path, *, job=True):
    h = Harness(tmp_path)
    await h.jobs.initialize()
    if job:
        await h.jobs.create(REQUEST, revision=h.jobs.revision, scope=SCOPE)
    return h


@pytest.mark.parametrize("cancel", [False, True])
def test_stop_waiting_for_owner_lock_fences_start_before_it_can_execute(tmp_path, cancel):
    async def run():
        h = await _harness(tmp_path)
        c = h.jobs
        owner = f"service-job:{c.job.identifier}"
        assert c.current_policy(owner, True) is True
        await c._lock.acquire()
        task = asyncio.create_task(c.stop())
        try:
            await asyncio.sleep(0)
            assert not task.done()
            assert c.stop_requested()
            assert c.snapshot()["manual_stop_pending"]
            assert c.current_policy(owner, True) is False
            assert c.current_policy(owner, False) is True
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                # A request that was cut off does not withdraw the stop.
                assert c.stop_requested()
                assert c.snapshot()["manual_stop_pending"]
        finally:
            c._lock.release()
        result = await (c.stop() if cancel else task)
        assert result["manual_stop_pending"] is False
        assert c.job.state == "stopped"
    asyncio.run(run())


def test_cancelled_stop_without_job_cannot_be_undone_by_new_preview(tmp_path):
    async def run():
        h = await _harness(tmp_path, job=False)
        c = h.jobs
        await c._lock.acquire()
        task = asyncio.create_task(c.stop())
        try:
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            c._lock.release()
        assert c.current_policy(None, True) is False
        assert c.current_policy(None, False) is None
        with pytest.raises(JobRequestError, match="job_stop_unconfirmed"):
            c.preview(REQUEST, revision=c.revision, scope=SCOPE)
        # A clear without a confirmed pause cannot remove the fence.
        with pytest.raises(JobRequestError, match="job_clear_requires_pause"):
            await c.clear(revision=c.revision)
        assert c.snapshot()["manual_stop_pending"]
        h.change(current_a=1.0)
        assert (await c.clear(revision=c.revision))["manual_stop_pending"] is False
        assert c.preview(REQUEST, revision=c.revision, scope=SCOPE)["scope"] == SCOPE
    asyncio.run(run())


def test_a_stop_with_an_outdated_revision_fences_nothing(tmp_path):
    async def run():
        h = await _harness(tmp_path)
        with pytest.raises(JobRequestError, match="job_changed"):
            await h.jobs.stop(revision="outdated")
        assert not h.jobs.snapshot()["manual_stop_pending"]
        assert h.jobs.job.state != "stopped"
    asyncio.run(run())
