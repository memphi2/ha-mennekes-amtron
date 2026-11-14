"""One shared durable draft, with no charging authority until confirmed."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.job_draft import JobDraft, validate
from custom_components.mennekes_amtron_rtu.device.service_job_http import job_operation
from tests.test_service_orders import NOW, SCOPE, Harness, _with_app


def test_draft_persists_without_arming_or_changing_a_job(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        assert jobs.snapshot()["draft"]["status"] == "needs_finish"
        revision = jobs.revision
        h.change(present=False)
        await jobs.edit_draft(
            {"finish_at": (NOW + timedelta(hours=2)).isoformat(), "current_a": 10},
            revision=jobs.draft.revision,
        )
        assert jobs.draft_status() == "job_not_permitted"
        assert jobs.revision == revision
        assert h.calls == []
        assert jobs.job is None
        again = Harness(tmp_path)
        await again.jobs.initialize()
        assert again.jobs.draft.values == jobs.draft.values
        assert again.jobs.draft.revision != jobs.draft.revision
        assert again.jobs.job is None
        h.change(present=True)
        preview = jobs.preview_draft(
            draft_revision=jobs.draft.revision, revision=revision, scope=SCOPE
        )
        await jobs.create_draft(
            preview["request"],
            draft_revision=preview["draft_revision"],
            revision=revision,
            scope=SCOPE,
        )
        job = jobs.job.snapshot()
        calls = list(h.calls)
        await jobs.edit_draft({"energy_kwh": 20}, revision=jobs.draft.revision)
        assert jobs.job.snapshot() == job
        assert h.calls == calls
        assert jobs.draft_status() == "job_exists"
        await job_operation(
            jobs,
            {
                "action": "reset_draft",
                "draft_revision": jobs.draft.revision,
                "confirm": True,
            },
        )
        assert jobs.draft.values["finish_at"] is None
        assert jobs.job.snapshot() == job
        assert h.calls == calls

    asyncio.run(run())


def test_stale_edits_and_preview_cannot_overwrite_or_start(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        old = jobs.draft.revision
        await jobs.edit_draft(
            {"finish_at": (NOW + timedelta(hours=2)).isoformat()}, revision=old
        )
        with pytest.raises(JobRequestError, match="job_changed"):
            await jobs.edit_draft({"current_a": 10}, revision=old)
        preview = jobs.preview_draft(
            draft_revision=jobs.draft.revision, revision=jobs.revision, scope=SCOPE
        )
        await jobs.edit_draft({"energy_kwh": 15}, revision=jobs.draft.revision)
        with pytest.raises(JobRequestError, match="job_changed"):
            await jobs.create_draft(
                preview["request"],
                draft_revision=preview["draft_revision"],
                revision=jobs.revision,
                scope=SCOPE,
            )
        preview = jobs.preview_draft(
            draft_revision=jobs.draft.revision, revision=jobs.revision, scope=SCOPE
        )
        with pytest.raises(JobRequestError, match="job_changed"):
            await jobs.create_draft(
                {**preview["request"], "energy_kwh": 16},
                draft_revision=preview["draft_revision"],
                revision=jobs.revision,
                scope=SCOPE,
            )
        assert h.calls == []
        assert jobs.job is None
        # Identical saves do not churn disk or invalidate the other surface.
        old = jobs.draft.revision
        jobs.draft.save = lambda _: pytest.fail("unchanged draft written")
        await jobs.edit_draft({"energy_kwh": 15}, revision=old)
        assert jobs.draft.revision == old

    asyncio.run(run())


@pytest.mark.parametrize("changes", [None, {}, {"alien": 1}])
def test_edit_allowlist(tmp_path, changes):
    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        with pytest.raises(JobRequestError, match="invalid_job_request"):
            await h.jobs.edit_draft(changes, revision=h.jobs.draft.revision)

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"current_a": True},
        {"current_a": 5.9},
        {"current_a": 33},
        {"current_a": float("nan")},
        {"energy_kwh": 0.11},
        {"energy_kwh": "10"},
        {"reserve_kwh": 11},
        {"finish_at": "invalid"},
        {"start_at": "2026-11-16T06:00:00"},
        {"energy_kwh": float("inf")},
    ],
)
def test_invalid_draft_values(tmp_path, changes):
    values = JobDraft(tmp_path / "draft.json", "id").values
    with pytest.raises(ValueError, match=r".+"):
        validate(values | changes)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"version": True, "identity": "id", "values": {}},
        {"version": 1, "identity": "other", "values": {}},
        "x" * 4097,
    ],
)
def test_corrupt_draft_is_bounded(tmp_path, raw):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=r".+"):
        JobDraft(path, "id").load()


def test_failed_storage_is_separate_and_explicit_reset_recovers(tmp_path, monkeypatch):
    async def run():
        (tmp_path / "job-draft.json").write_text("broken")
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        assert jobs.draft_status() == "job_storage_unavailable"
        assert not jobs.storage_error
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            jobs.preview_draft(
                draft_revision=jobs.draft.revision, revision=jobs.revision, scope=SCOPE
            )
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await jobs.create_draft(
                {},
                draft_revision=jobs.draft.revision,
                revision=jobs.revision,
                scope=SCOPE,
            )
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await jobs.edit_draft({"current_a": 8}, revision=jobs.draft.revision)
        await job_operation(
            jobs,
            {
                "action": "reset_draft",
                "draft_revision": jobs.draft.revision,
                "confirm": True,
            },
        )
        assert not jobs.draft.storage_error
        revision = jobs.draft.revision
        with monkeypatch.context() as patch:

            def broken(_):
                raise OSError("disk failed")

            patch.setattr(jobs.draft, "save", broken)
            with pytest.raises(JobRequestError, match="job_storage_unavailable"):
                await jobs.edit_draft({"energy_kwh": 20}, revision=revision)
        assert jobs.draft.storage_error
        assert jobs.draft.revision == revision
        assert jobs.draft.values["energy_kwh"] == 10
        assert h.calls == []

    asyncio.run(run())


def test_live_cap_read_only_and_expired_window(tmp_path):
    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        with pytest.raises(JobRequestError, match="job_current_exceeds_cap"):
            await jobs.edit_draft({"current_a": 20}, revision=jobs.draft.revision)
        await jobs.edit_draft(
            {
                "start_at": (NOW - timedelta(hours=1)).isoformat(),
                "finish_at": (NOW + timedelta(hours=1)).isoformat(),
            },
            revision=jobs.draft.revision,
        )
        assert jobs.draft_status() == "job_preview_expired"
        await jobs.edit_draft(
            {"start_at": None, "finish_at": (NOW - timedelta(hours=1)).isoformat()},
            revision=jobs.draft.revision,
        )
        assert jobs.draft_status() == "invalid_job_request"
        await jobs.edit_draft(
            {"finish_at": (NOW + timedelta(hours=1)).isoformat()},
            revision=jobs.draft.revision,
        )
        assert jobs.draft_status() == "ready"
        h.change(read_only=True)
        assert jobs.draft_status() == "job_not_permitted"
        with pytest.raises(JobRequestError, match="job_not_permitted"):
            await jobs.edit_draft({"current_a": 8}, revision=jobs.draft.revision)
        assert h.calls == []

    asyncio.run(run())


def test_home_assistant_uses_real_shared_api_and_stale_cache_is_refreshed(tmp_path):
    async def body(h, orders, refreshed):
        await orders.async_edit_draft(
            "finish_at", (NOW + timedelta(hours=2)).isoformat()
        )
        assert orders.draft["status"] == "ready"
        await h.jobs.edit_draft({"energy_kwh": 20}, revision=h.jobs.draft.revision)
        with pytest.raises(HomeAssistantError) as err:
            await orders.async_edit_draft("current_a", 8)
        assert err.value.translation_key == "job_changed"
        assert orders.draft["values"]["energy_kwh"] == 20
        assert h.calls == []
        await orders.async_start_draft()
        assert h.jobs.job.energy.target_kwh == 20
        await orders.async_reset_draft()
        assert orders.draft["values"]["finish_at"] is None
        assert h.jobs.job.energy.target_kwh == 20
        assert refreshed
        orders._client.snapshot = {}
        assert orders.draft is None
        for call in (
            orders.async_edit_draft("current_a", 8),
            orders.async_start_draft(),
            orders.async_reset_draft(),
        ):
            with pytest.raises(HomeAssistantError):
                await call
        for draft in (
            {},
            {
                "revision": "a" * 32,
                "storage_error": False,
                "status": "ready",
                "values": {},
            },
        ):
            orders._client.snapshot = {"control": {"orders": {"draft": draft}}}
            assert orders.draft is None

    asyncio.run(_with_app(tmp_path, body))


def test_cancelled_save_finishes_before_another_editor_can_enter(tmp_path, monkeypatch):
    import threading

    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        started, release = threading.Event(), threading.Event()
        original = jobs.draft.save

        def delayed(values):
            started.set()
            assert release.wait(5)
            original(values)

        monkeypatch.setattr(jobs.draft, "save", delayed)
        old = jobs.draft.revision
        task = asyncio.create_task(jobs.edit_draft({"energy_kwh": 20}, revision=old))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            second = asyncio.create_task(
                jobs.edit_draft({"current_a": 10}, revision=old)
            )
            await asyncio.sleep(0)
            assert not second.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(JobRequestError, match="job_changed"):
            await second
        restored = JobDraft(jobs.draft.path, "fixture")
        restored.load()
        assert restored.values == jobs.draft.values
        assert restored.values["energy_kwh"] == 20
        assert h.calls == []

    asyncio.run(run())


def test_remote_draft_changes_notify_entities_without_register_changes(tmp_path):
    from copy import deepcopy

    from aiohttp import ClientSession

    from custom_components.mennekes_amtron_rtu.remote import RemoteClient
    from tests.test_modbus_service import TOKEN, service_fixture

    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = RemoteClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                before = client.apply_snapshot(payload)
                draft = JobDraft(tmp_path / "draft.json", "fixture").snapshot()
                draft["status"] = "needs_finish"
                payload["control"]["orders"] = {"draft": draft}
                first = client.apply_snapshot(payload)
                assert before != first
                assert first == client.apply_snapshot(deepcopy(payload))
                draft["values"]["energy_kwh"] = 25
                changed = client.apply_snapshot(payload)
                assert changed != first
                assert first.service_job_draft["values"]["energy_kwh"] == 10
                assert changed.values == first.values
        finally:
            await service.stop()
    asyncio.run(run())
