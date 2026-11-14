"""Guest edits are separate, durable and tied to the previewed connection."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.guest_draft import (
    GuestDraft,
    defaults,
    read_snapshot,
    validate,
)
from custom_components.mennekes_amtron_rtu.device.service_job_http import job_operation
from tests.test_service_orders import NOW, SCOPE, Harness, _with_app


def test_guest_draft_separate_persistence_and_frozen_confirmation(tmp_path):
    async def body():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        await job_operation(jobs, {
            "action": "edit_guest_draft", "draft_revision": jobs.guest_draft.revision,
            "changes": {"energy_kwh": 15, "duration_minutes": 180, "current_a": 8},
        })
        assert h.calls == []
        assert jobs.job is None
        restored = Harness(tmp_path)
        await restored.jobs.initialize()
        assert restored.jobs.guest_draft.values == jobs.guest_draft.values
        assert restored.jobs.guest_draft.revision != jobs.guest_draft.revision
        assert restored.jobs.job is None
        preview = await job_operation(jobs, {
            "action": "preview_guest_draft", "draft_revision": jobs.guest_draft.revision,
            "revision": jobs.revision, "scope": SCOPE,
        })
        assert preview["request"]["finish_at"] == (NOW + timedelta(hours=3)).isoformat()
        h.change(now=NOW + timedelta(seconds=20))
        await job_operation(jobs, {
            "action": "create_guest_draft", "draft_revision": preview["draft_revision"],
            "revision": preview["revision"], "scope": preview["scope"], "request": preview["request"],
        })
        assert jobs.job.kind == "guest"
        assert jobs.job.finish_at == NOW + timedelta(hours=3)
        active = jobs.job.snapshot()
        await job_operation(jobs, {"action": "reset_guest_draft", "draft_revision": jobs.guest_draft.revision, "confirm": True})
        assert jobs.guest_draft.values == defaults()
        assert jobs.job.snapshot() == active
    asyncio.run(body())


@pytest.mark.parametrize("change", [{"duration_minutes": True}, {"duration_minutes": 1.5}, {"duration_minutes": 0}, {"duration_minutes": 1441}, {"energy_kwh": float("nan")}, {"current_a": 5}, {"finish_at": None}])
def test_guest_draft_rejects_invalid_inputs(change):
    with pytest.raises(ValueError, match="invalid"):

        validate({**defaults(), **change})


def test_guest_preview_rejects_stale_edits_and_tampered_deadlines(tmp_path):
    async def body():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        preview = jobs.preview_draft(draft_revision=jobs.guest_draft.revision, revision=jobs.revision, scope=SCOPE)
        bad = {**preview["request"], "finish_at": (NOW + timedelta(hours=4)).isoformat()}
        with pytest.raises(JobRequestError, match="job_changed"):
            await jobs.create_draft(bad, draft_revision=preview["draft_revision"], revision=preview["revision"], scope=SCOPE)
        await jobs.edit_draft({"energy_kwh": 20}, revision=jobs.guest_draft.revision)
        with pytest.raises(JobRequestError, match="job_changed"):
            await jobs.create_draft(preview["request"], draft_revision=preview["draft_revision"], revision=preview["revision"], scope=SCOPE)
        assert jobs.job is None
        assert h.calls == []
        h.change(vehicle_target_active=True)
        assert jobs.draft_status() == "guest_vehicle_target_active"
        assert read_snapshot(jobs.snapshot()["guest_draft"]) is not None
    asyncio.run(body())


def test_ha_guest_controls_share_the_app_draft(tmp_path):
    async def body(h, orders, _):
        from custom_components.mennekes_amtron_rtu import button, number, sensor
        from tests.ha_fakes import build_runtime, collect
        hass, entry, _transport = await build_runtime()
        entry.runtime_data.orders = orders
        entities = {}
        for platform in (button, number, sensor):
            await platform.async_setup_entry(hass, entry, lambda added: entities.update(collect(added)))
        duration = entities["guest_duration_minutes"]
        assert duration.available
        assert duration.native_value == 120
        assert duration.native_max_value == 1440
        await duration.async_set_native_value(180)
        await entities["guest_energy_kwh"].async_set_native_value(20)
        assert orders.guest_draft["values"]["duration_minutes"] == 180
        await entities["guest_start"].async_press()
        assert h.jobs.job.kind == "guest"
        await entities["guest_stop"].async_press()
        await entities["guest_clear"].async_press()
        await entities["guest_reset_draft"].async_press()
        assert duration.native_value == 120
    asyncio.run(_with_app(tmp_path, body))


@pytest.mark.parametrize("changes", [None, {}, {"alien": 1}])
def test_edit_allowlist(tmp_path, changes):
    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        with pytest.raises(JobRequestError, match="invalid_job_request"):
            await h.jobs.edit_draft(changes, revision=h.jobs.guest_draft.revision)

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
    values = GuestDraft(tmp_path / "draft.json", "id").values
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
        GuestDraft(path, "id").load()


def test_failed_storage_is_separate_and_explicit_reset_recovers(tmp_path, monkeypatch):
    async def run():
        (tmp_path / "guest-draft.json").write_text("broken")
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        assert jobs.draft_status() == "job_storage_unavailable"
        assert not jobs.storage_error
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            jobs.preview_draft(
                draft_revision=jobs.guest_draft.revision, revision=jobs.revision, scope=SCOPE
            )
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await jobs.create_draft(
                {},
                draft_revision=jobs.guest_draft.revision,
                revision=jobs.revision,
                scope=SCOPE,
            )
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await jobs.edit_draft({"current_a": 8}, revision=jobs.guest_draft.revision)
        await job_operation(
            jobs,
            {
                "action": "reset_guest_draft",
                "draft_revision": jobs.guest_draft.revision,
                "confirm": True,
            },
        )
        assert not jobs.guest_draft.storage_error
        revision = jobs.guest_draft.revision
        with monkeypatch.context() as patch:

            def broken(_):
                raise OSError("disk failed")

            patch.setattr(jobs.guest_draft, "save", broken)
            with pytest.raises(JobRequestError, match="job_storage_unavailable"):
                await jobs.edit_draft({"energy_kwh": 20}, revision=revision)
        assert jobs.guest_draft.storage_error
        assert jobs.guest_draft.revision == revision
        assert jobs.guest_draft.values["energy_kwh"] == 10
        assert h.calls == []

    asyncio.run(run())




def test_cancelled_save_finishes_before_another_editor_can_enter(tmp_path, monkeypatch):
    import threading

    async def run():
        h = Harness(tmp_path)
        await h.jobs.initialize()
        jobs = h.jobs
        started, release = threading.Event(), threading.Event()
        original = jobs.guest_draft.save

        def delayed(values):
            started.set()
            assert release.wait(5)
            original(values)

        monkeypatch.setattr(jobs.guest_draft, "save", delayed)
        old = jobs.guest_draft.revision
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
        restored = GuestDraft(jobs.guest_draft.path, "fixture")
        restored.load()
        assert restored.values == jobs.guest_draft.values
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
                draft = GuestDraft(tmp_path / "draft.json", "fixture").snapshot()
                draft["status"] = "ready"
                payload["control"]["orders"] = {"guest_draft": draft}
                first = client.apply_snapshot(payload)
                assert before != first
                assert first == client.apply_snapshot(deepcopy(payload))
                draft["values"]["energy_kwh"] = 25
                changed = client.apply_snapshot(payload)
                assert changed != first
                assert first.service_guest_draft["values"]["energy_kwh"] == 10
                assert changed.values == first.values
        finally:
            await service.stop()
    asyncio.run(run())


def test_oversized_integer_is_invalid_not_an_internal_error(tmp_path):
    values = GuestDraft(tmp_path / "draft.json", "fixture").values
    with pytest.raises(ValueError, match=r"invalid guest draft value"):
        validate(values | {"energy_kwh": 10**400})


def test_remote_draft_does_not_copy_untrusted_extra_nesting(tmp_path):
    from aiohttp import ClientSession

    from custom_components.mennekes_amtron_rtu.remote import RemoteClient
    from tests.test_modbus_service import TOKEN, service_fixture

    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = RemoteClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                draft = GuestDraft(tmp_path / "draft.json", "fixture").snapshot()
                draft["status"] = "ready"
                nested = []
                for _ in range(600):
                    nested = [nested]
                draft["unexpected"] = nested
                payload["control"]["orders"] = {"guest_draft": draft}
                data = client.apply_snapshot(payload)
                assert data.service_guest_draft is None
        finally:
            await service.stop()
    asyncio.run(run())


def test_missing_shared_draft_refuses_all_ha_edits(tmp_path):
    from homeassistant.exceptions import HomeAssistantError
    async def body(_h, orders, _refreshed):
        orders._client.snapshot = {}
        for call in (lambda: orders.async_edit_draft("current_a", 8), orders.async_start_draft, orders.async_reset_draft):
            with pytest.raises(HomeAssistantError) as error:
                await call()
            assert error.value.translation_key == "service_order_failed"
        invalid = {"revision": "a" * 32, "storage_error": False, "status": "ready", "values": {**defaults(), "energy_kwh": []}}
        assert read_snapshot(invalid) is None
    asyncio.run(_with_app(tmp_path, body))
