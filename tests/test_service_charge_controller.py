"""Durable job intent is independent of UI clients and hardware acknowledgements."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    ChargeJobController,
    JobInputs,
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_store import ChargeJobStore
from custom_components.mennekes_amtron_rtu.device.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError

NOW = datetime(2026, 11, 13, 18, tzinfo=UTC)
SCOPE = "a" * 64


class Harness:
    def __init__(self, path):
        self.sample = JobInputs(NOW, True, True, SCOPE, 100.0, True, True, False, 8.0, 16.0)
        self.calls = []
        self.error = None
        self.store = ChargeJobStore(path / "charge-job.json", "fixture")
        self.controller = ChargeJobController(self.store, lambda: self.sample, self.apply)

    def change(self, **values):
        self.sample = replace(self.sample, **values)

    async def apply(self, value, owner, deadline):
        self.calls.append((value, owner, deadline))
        if self.error:
            raise self.error
        self.change(current_a=1.0 if value is None else value, pending=False)

    def request(self, *, delay=0, **values):
        return {
            "id": "b" * 32, "start_at": (NOW + timedelta(seconds=delay)).isoformat(),
            "finish_at": (NOW + timedelta(hours=4)).isoformat(),
            "current_a": 12.0, "energy_kwh": 10.0, **values,
        }

    async def create(self, **values):
        c = self.controller
        return await c.create(self.request(**values), revision=c.revision, scope=self.sample.scope)


@pytest.mark.parametrize("delay", [0, 3600])
def test_persisted_order_runs_without_an_ha_client_and_stops_at_its_ceiling(tmp_path, delay):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        assert c.stop_requested()
        assert c.current_policy(None, True) is False
        await c.initialize()
        assert c.current_policy(None, True) is None
        assert not c.stop_requested()
        preview = c.preview(h.request(delay=delay), revision=c.revision, scope=SCOPE)
        assert not h.store.path.exists()
        assert h.calls == []
        result = await c.create(preview["request"], revision=preview["revision"], scope=SCOPE)
        assert result["job"]["state"] == ("scheduled" if delay else "charging")
        assert h.calls[-1][0] == (None if delay else 12)
        assert h.store.load().reason == "service_restarted"
        assert c.current_policy("other", True) is False
        assert c.current_policy("other", False) is None
        assert c.current_policy("service-job:" + "b" * 32, False) is True
        h.change(now=NOW + timedelta(hours=1, seconds=1))
        await c.tick()
        assert c.job.state == "charging"
        assert c.current_policy("service-job:" + "b" * 32, True) is True
        h.change(total_kwh=110)
        c.observe()
        assert c.stop_requested()
        await c.tick()
        assert c.job.state == "reached"
        assert h.calls[-1][0] is None
        h.change(total_kwh=105)
        await c.tick()
        assert c.job.state == "reached"
        assert c.snapshot()["job"]["delivered_kwh"] == 5
        assert "binding" not in c.snapshot()["job"]
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": False}, {"present": None}, {"scope": None},
    {"meter_fresh": False}, {"permitted": False}, {"independent_stop": True}, {"pending": True},
])
def test_admission_requires_fresh_consistent_permission(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        h.change(**change)
        with pytest.raises(JobRequestError, match="job_not_permitted"):
            await h.create()
        assert h.calls == []
        assert not h.store.path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("revision", [None, True, "", "stale"])
def test_stale_confirmation_is_not_a_command(tmp_path, revision):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        with pytest.raises(JobRequestError, match="job_changed"):
            await h.controller.create(h.request(), revision=revision, scope=SCOPE)
        assert h.calls == []
    asyncio.run(run())


def test_current_cap_scope_existing_job_and_idempotency(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        with pytest.raises(JobRequestError, match="job_current_exceeds_cap"):
            await h.create(current_a=20)
        with pytest.raises(JobRequestError, match="job_not_permitted"):
            await c.create(h.request(), revision=c.revision, scope="old")
        await h.create()
        before = list(h.calls)
        assert (await h.create())["job"]["state"] == "charging"
        assert h.calls == before
        with pytest.raises(JobRequestError, match="job_exists"):
            await h.create(id="c" * 32)
        await c.stop(revision=c.revision)
        assert (await h.create())["job"]["state"] == "stopped"
        assert h.calls[-1][0] is None
        await c.clear(revision=c.revision)
        assert c.job is None
        assert c.current_policy(None, True) is None
        assert not c.stop_requested()
        assert h.calls[-1][0] is None
    asyncio.run(run())


@pytest.mark.parametrize("condition", ["startup", "closing", "storage"])
def test_unavailable_owner_cannot_grant_or_preview(tmp_path, condition):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        if condition != "startup":
            await c.initialize()
        if condition == "closing":
            await c.shutdown()
        if condition == "storage":
            c.storage_error = True
        assert c.stop_requested()
        assert c.current_policy("x", True) is False
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await h.create()
        assert c.snapshot()["job"] is None
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": None}, {"scope": "c" * 64},
    {"meter_fresh": False}, {"permitted": False}, {"independent_stop": True},
    {"now": NOW + timedelta(hours=5)},
])
def test_prewrite_guard_does_not_wait_for_the_next_tick(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        assert not c.stop_requested()
        h.change(**change)
        assert c.stop_requested()
        assert c.current_policy("service-job:" + "b" * 32, True) is False
        before = len(h.calls)
        await c.tick()
        applied = [value for value, _owner, _deadline in h.calls[before:]]
        if h.sample.available and h.sample.present is True:
            # The tick enforces the stop the guard already reported.
            assert applied == [None]
        else:
            # Without a readable car nothing is written, a start least of all.
            assert applied == []
    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError("disk"), ValueError("corrupt")])
def test_corrupt_store_is_visible_and_requires_explicit_safe_reset(tmp_path, monkeypatch, error):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        monkeypatch.setattr(h.store, "load", Mock(side_effect=error))
        await c.initialize()
        assert c.storage_error
        await c.tick()
        assert h.calls[-1][0] is None
        assert c.stop_requested()
        await c.clear(revision=c.revision)
        assert not c.storage_error
        assert not c.stop_requested()
    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError("disk"), ValueError("format")])
def test_failed_save_never_enables_and_recovery_never_rearms(tmp_path, monkeypatch, error):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        original = h.store.save
        monkeypatch.setattr(h.store, "save", Mock(side_effect=error))
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await h.create()
        assert c.job.reason == "storage_error"
        assert h.calls[-1][0] is None
        monkeypatch.setattr(h.store, "save", original)
        await c.tick()
        assert not c.storage_error
        assert c.job.terminal
        # Once durable and paused, the order hands control back; it never
        # writes a charging current itself.
        assert not c.stop_requested()
        assert h.store.load() is None
        assert all(call[0] is None for call in h.calls)
    asyncio.run(run())


@pytest.mark.parametrize("error", [OSError("disk"), ValueError("format")])
def test_failed_clear_preserves_the_stop(tmp_path, monkeypatch, error):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        await c.stop()
        monkeypatch.setattr(h.store, "save", Mock(side_effect=error))
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await c.clear(revision=c.revision)
        assert c.job is not None
        assert c.stop_requested()
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"available": False}, {"pending": True}, {"current_a": 12}, {"current_a": None}])
def test_cannot_clear_unconfirmed_running_order(tmp_path, change):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        await c.stop()
        h.change(**change)
        with pytest.raises(JobRequestError, match="job_clear_requires_pause"):
            await c.clear(revision=c.revision)
        assert c.holding
    asyncio.run(run())


def test_unplug_ends_scope_but_never_writes_to_the_next_vehicle(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        h.change(present=False, scope=None, current_a=None)
        before = list(h.calls)
        await c.tick()
        assert c.job.scope_ended
        assert not c.holding
        assert h.calls == before
        assert c.current_policy(None, True) is None
        assert c.snapshot()["job"]["delivered_kwh"] is None
        await c.clear(revision=c.revision)
        assert c.job is None
    asyncio.run(run())


@pytest.mark.parametrize("error", [AmtronBusError(), DeviceError("write_failed"), OSError("socket"), ValueError("command")])
def test_uncertain_actuation_is_not_reported_as_a_confirmed_stop(tmp_path, error):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        h.error = error
        await h.create()
        assert c.action_error == "command_unconfirmed"
        h.error = None
        await c.tick()
        assert c.action_error is None
        h.error = error
        await c.stop()
        assert c.action_error == "command_unconfirmed"
        h.error = None
        await c.tick()
        assert h.calls[-1][0] is None
        assert c.action_error is None
    asyncio.run(run())


def test_pending_commands_coalesce_but_new_stops_still_cancel_starts(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        before = list(h.calls)
        h.change(current_a=8, pending=True)
        await c.tick()
        assert h.calls == before
        await c.stop()
        assert h.calls[-1][0] is None
        before = list(h.calls)
        await c.tick()
        assert h.calls == before
    asyncio.run(run())


def test_readback_clears_error_and_shutdown_never_creates_a_restart_permission(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        c.observe()
        await c.tick()
        with pytest.raises(JobRequestError, match="job_clear_requires_pause"):
            await c.clear(revision=c.revision)
        await c.initialize()
        h.change(current_a=1)
        await h.create()
        h.change(current_a=1)
        with pytest.raises(JobRequestError, match="job_stop_first"):
            await c.clear(revision=c.revision)
        h.change(current_a=12)
        c.action_error = "command_unconfirmed"
        await c.tick()
        assert c.action_error is None
        await c.shutdown()
        assert c.job.reason == "service_restarted"
        assert h.calls[-1][0] is None
        loaded = ChargeJobController(h.store, lambda: h.sample, h.apply)
        await loaded.initialize()
        assert loaded.job.terminal
        # The restored order pauses first and hands control back only after
        # the wallbox confirmed that pause.
        h.change(current_a=12)
        before = len(h.calls)
        await loaded.tick()
        assert loaded.stop_requested()
        assert [call[0] for call in h.calls[before:]] == [None]
        await loaded.tick()
        assert not loaded.stop_requested()
        assert h.store.load() is None
        empty = Harness(tmp_path / "other").controller
        await empty.initialize()
        await empty.stop()
        await empty.shutdown()
    asyncio.run(run())


def test_transitions_during_save_cannot_publish_stale_resume_permission(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        original = h.store.save
        loop = asyncio.get_running_loop()
        def save(job):
            # The observation runs on the event loop while the worker saves a copy.
            loop.call_soon_threadsafe(lambda: (h.change(independent_stop=True), c.observe()))
            original(job)
        monkeypatch.setattr(h.store, "save", save)
        await h.create()
        assert c.job.reason == "independent_stop"
        assert c.stop_requested()
        # Only the stop is applied, never the start the save would have allowed.
        assert [value for value, _owner, _deadline in h.calls] == [None]
    asyncio.run(run())


def test_a_float32_readback_counts_as_the_applied_job_current(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create(current_a=10.1)
        assert h.calls[-1][0] == 10.1
        # The register stores float32; 10.1 A reads back slightly higher.
        h.change(current_a=10.100000381469727)
        before = list(h.calls)
        await c.tick()
        await c.tick()
        assert h.calls == before
        h.change(current_a=10.2)
        await c.tick()
        assert h.calls[-1][0] == 10.1
    asyncio.run(run())


def test_an_old_preview_asks_for_a_new_one(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        with pytest.raises(JobRequestError, match="job_preview_expired"):
            await h.create(delay=-61)
        # Other invalid requests keep their generic error.
        with pytest.raises(ValueError, match="current") as error:
            await h.create(current_a=40)
        assert not isinstance(error.value, JobRequestError)
        with pytest.raises(ValueError, match="time") as error:
            await h.create(start_at="not a time")
        assert not isinstance(error.value, JobRequestError)
        assert not h.calls
    asyncio.run(run())


def test_a_failed_store_fences_only_starts(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        # Before the store is read, and after it failed, a pause still runs.
        assert c.current_policy("ha-departure", False) is None
        assert c.current_policy("ha-departure", True) is False

        def nested():
            raise RecursionError

        monkeypatch.setattr(h.store, "load", nested)
        await c.initialize()
        assert c.storage_error
        assert c.current_policy("ha-departure", False) is None
        assert c.current_policy(None, True) is False
    asyncio.run(run())


def test_finished_order_hands_control_back_once_its_pause_is_confirmed(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        assert c.snapshot()["job"]["holding"] is True
        h.change(total_kwh=110)
        await c.tick()
        assert c.job.state == "reached"
        assert h.calls[-1][0] is None
        # The tick that pauses only requests it; the next one sees the pause.
        assert c.stop_requested()
        assert h.store.load().state == "reached"
        await c.tick()
        assert not c.holding
        assert not c.stop_requested()
        assert c.current_policy(None, True) is None
        assert h.store.load() is None
        job = c.snapshot()["job"]
        assert (job["state"], job["holding"]) == ("reached", False)
        # HA resumes; the finished order neither pauses again nor saves.
        h.change(current_a=16.0)
        calls = list(h.calls)
        await c.tick()
        assert h.calls == calls
        assert h.store.load() is None
        restarted = ChargeJobController(h.store, lambda: h.sample, h.apply)
        await restarted.initialize()
        assert restarted.job is None
        assert not restarted.stop_requested()
        # A finished order is cleared without a pause, or replaced directly.
        replaced = await c.create(h.request(id="c" * 32), revision=c.revision, scope=SCOPE)
        assert replaced["job"]["id"] == "c" * 32
        assert replaced["job"]["holding"] is True
        assert h.store.load().identifier == "c" * 32
        h.change(total_kwh=125)
        await c.tick()
        await c.tick()
        h.change(current_a=16.0)
        assert not c.holding
        assert (await c.clear(revision=c.revision))["job"] is None
        assert h.calls[-1][0] is None
    asyncio.run(run())


def test_finished_order_keeps_holding_until_the_pause_is_confirmed_and_saved(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create()
        await c.stop()
        assert c.job.state == "stopped"
        # While it holds the pause, a stopped order blocks a new one.
        with pytest.raises(JobRequestError, match="job_exists"):
            await h.create(id="c" * 32)
        h.change(pending=True)
        await c.tick()
        assert c.holding
        h.change(pending=False, current_a=12.0)
        h.error = AmtronBusError("busy")
        await c.tick()
        assert c.holding
        h.error = None
        h.change(current_a=1.0)
        original = h.store.save
        monkeypatch.setattr(h.store, "save", Mock(side_effect=OSError("disk")))
        await c.tick()
        assert c.holding
        assert c.stop_requested()
        # The finished order was already durable, so the failed hand-back
        # does not count as a storage error; the next tick tries again.
        assert not c.storage_error
        monkeypatch.setattr(h.store, "save", original)
        await c.tick()
        assert not c.holding
        assert h.store.load() is None
    asyncio.run(run())
