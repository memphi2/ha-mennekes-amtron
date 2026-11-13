"""The real service uses the weekly owner without a second Modbus connection."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu.device import service_weekly_runtime as m
from tests import test_service_charge_runtime as runtime
from tests.test_weekly_controller import PLAN, recipe


def test_weekly_activation_runs_through_the_existing_guarded_service(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(runtime, "JobModbusService", m.WeeklyModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, clock):
            now = datetime.now(UTC)
            finish = now + timedelta(hours=2)
            w = service.weekly
            await w.save_plan(recipe(departure_time=finish.strftime("%H:%M"), charge_minutes=30), revision=w.revision)
            before = list(transport.writes)
            p = w.preview_activation(PLAN, "fixture", revision=w.revision, scope=service.jobs.inputs().scope)
            assert transport.writes == before
            accepted = await w.activate(p)
            assert accepted["active"]["plan_id"] == PLAN
            assert runtime.current_writes(transport)[-1] == 1
            # HA disconnect/reload cancels only its own unrelated timed owner.
            await service.command({"command": "cancel_timed_current", "owner": "ha-departure", "expected_serial_number": service.identity.serial_number})
            assert w.consent is not None
            assert service.jobs.manual_stop_epoch == 0
            original = service.jobs.inputs
            service.jobs.inputs = lambda: replace(original(), now=finish - timedelta(minutes=15))
            clock[0] += 1000
            await service.jobs.tick()
            assert runtime.current_writes(transport)[-1] == 12
            clock[0] += 1000
            await w.deactivate(revision=w.revision)
            assert runtime.current_writes(transport)[-1] == 1
            assert service.jobs.job.state == "stopped"
            assert w.consent is None
            assert all(address == runtime.R.CHARGING_CURRENT_EMS.address for address, _words in transport.writes)
    asyncio.run(run())


def test_bad_weekly_storage_does_not_restart_the_working_bus(tmp_path, monkeypatch):
    async def run():
        (tmp_path / "weekly-plans.json").write_text("{")
        monkeypatch.setattr(runtime, "JobModbusService", m.WeeklyModbusService)
        async with runtime.job_service(tmp_path) as (service, _transport, _clock):
            assert service.jobs.ready
            assert service.weekly.storage_error
            assert service._is_available()
            await service.weekly.tick()
            assert service.jobs.job is None
    asyncio.run(run())


@pytest.mark.parametrize("error", [False, True])
def test_weekly_loop_is_bounded_and_keeps_job_decisions_first(monkeypatch, error):
    async def run():
        calls = []
        service = m.WeeklyModbusService.__new__(m.WeeklyModbusService)
        service.jobs = SimpleNamespace(tick=AsyncMock(side_effect=lambda: calls.append("jobs")), storage_error=error, action_error=None)
        service.weekly = SimpleNamespace(tick=AsyncMock(side_effect=lambda: calls.append("weekly")))
        sleep = AsyncMock(side_effect=asyncio.CancelledError)
        monkeypatch.setattr(m.asyncio, "sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await service._jobs_loop()
        assert calls == ["jobs", "weekly"]
        sleep.assert_awaited_once_with(5 if error else 1)
    asyncio.run(run())


def test_starting_loop_and_unconfigured_owner_fail_safely(monkeypatch):
    async def run():
        service = m.WeeklyModbusService.__new__(m.WeeklyModbusService)
        service.jobs, service.weekly = None, None
        monkeypatch.setattr(m.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await service._jobs_loop()
        monkeypatch.setattr(m.JobModbusService, "_initialize", AsyncMock())
        with pytest.raises(RuntimeError, match="weekly controller"):
            await service._initialize()
        stopping = AsyncMock()
        monkeypatch.setattr(m.JobModbusService, "stop", stopping)
        await service.stop()
        stopping.assert_awaited_once()
        service.weekly = SimpleNamespace(shutdown=AsyncMock(side_effect=OSError("fixture")))
        with pytest.raises(OSError, match="fixture"):
            await service.stop()
        assert stopping.await_count == 2
    asyncio.run(run())
