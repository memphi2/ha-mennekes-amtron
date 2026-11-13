"""Forecast observation and storage cannot become a second command owner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import server
from custom_components.mennekes_amtron_rtu.device import service_forecast_runtime as m
from custom_components.mennekes_amtron_rtu.device import service_job_runtime as job_runtime
from custom_components.mennekes_amtron_rtu.device import service_runtime as bus_runtime
from custom_components.mennekes_amtron_rtu.device.charge_rate_store import RateStore
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.service_weekly_http import (
    create_weekly_app,
)
from tests import test_service_charge_runtime as runtime
from tests.test_charge_rate import AT, KEY, SCOPE, sample, train


def bare(path):
    service = m.ForecastModbusService.__new__(m.ForecastModbusService)
    service.rates, _ = train()
    service.forecast_clock = lambda: 2000
    service._rate_store = RateStore(path / "charge-rates.json", "fixture")
    service._rate_save = None
    service._rate_saved_generation = 0
    service._rate_save_at = 0
    service._rate_storage_error = False
    service._forecast_closing = False
    return service


def test_real_service_learns_without_extra_reads_writes_or_credentials(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(runtime, "JobModbusService", m.ForecastModbusService)
        async with runtime.job_service(tmp_path) as (s, transport, clock):
            assert server.ModbusService is m.ForecastModbusService
            assert isinstance(s.jobs, m.ForecastJobs)
            s.control.soc_goal.configure_source("fixture-private-profile")
            await s.refresh()
            await s.jobs.create(runtime.request(), revision=s.jobs.revision, scope=s.jobs.inputs().scope)
            baseline = s.jobs.job.energy.baseline_kwh
            now = [datetime.now(UTC)]

            class TestDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return now[0] if tz is not None else now[0].replace(tzinfo=None)

            monkeypatch.setattr(job_runtime, "datetime", TestDateTime)
            monkeypatch.setattr(bus_runtime, "wall_clock", lambda: now[0].timestamp())
            s.forecast_clock = lambda: clock[0]
            clock[0] += 1000
            runtime.put(transport.bank, runtime.R.EVSE_STATE, 5)
            runtime.put(transport.bank, runtime.R.DETECTED_EV_PHASES, 3)
            for index in range(4):
                if index:
                    now[0] += timedelta(seconds=60)
                    clock[0] += 60
                runtime.put(transport.bank, runtime.R.ENERGY_TOTAL, baseline + index * 0.15)
                s._poller._slow_read_at = None
                await s.refresh()
            before = (list(transport.reads), list(transport.writes), s.jobs.revision)
            for _ in range(5):
                result = s.jobs.snapshot()["forecast"]
                assert result["state"] == "estimate"
                assert result["source"] == "live"
                assert result["power_kw"] == 9
                assert "fixture-private-profile" not in json.dumps(result)
            assert (transport.reads, transport.writes, s.jobs.revision) == before
            async with TestClient(TestServer(create_weekly_app(s))) as client:
                assert (await client.get("/v2/jobs")).status == 401
                response = await client.get("/v2/jobs", headers={"Authorization": f"Bearer {runtime.TOKEN}"})
                assert response.status == 200
                assert (await response.json())["forecast"]["state"] == "estimate"
            assert (transport.reads, transport.writes, s.jobs.revision) == before
            s._start_rate_save(force=True)
            await s._rate_save
            assert len(s._rate_store.load().snapshot()) == 1
            assert "fixture-private-profile" not in s._rate_store.path.read_text()
            now[0] += timedelta(seconds=181)
            clock[0] += 1000
            assert s.jobs.snapshot()["forecast"]["state"] == "unavailable"
    asyncio.run(run())


def test_unusable_advisory_file_does_not_disable_the_real_service(tmp_path, monkeypatch):
    async def run():
        (tmp_path / "charge-rates.json").write_text("{")
        monkeypatch.setattr(runtime, "JobModbusService", m.ForecastModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, _clock):
            assert service._rate_storage_error
            assert service.jobs.ready
            assert service.weekly.ready
            assert service._is_available()
            assert service.jobs.snapshot()["forecast"]["history_storage"] == "unavailable"
            assert transport.writes == []
    asyncio.run(run())


def test_optional_save_is_coalesced_rate_limited_and_only_retries_when_requested(tmp_path, monkeypatch, caplog):
    async def run():
        s = bare(tmp_path)
        s._start_rate_save()
        first = s._rate_save
        s._start_rate_save()
        assert s._rate_save is first
        await first
        assert s._rate_saved_generation == s.rates.history.generation
        s._start_rate_save(force=True)
        assert s._rate_save is first
        s.rates.history.add(KEY, 9, AT + 240)
        s._start_rate_save()
        assert s._rate_save is first
        s._forecast_closing = True
        s._start_rate_save()
        assert s._rate_save is first
        original = s._rate_store.save
        monkeypatch.setattr(s._rate_store, "save", Mock(side_effect=OSError("private-storage-detail")))
        s._start_rate_save(force=True)
        await s._rate_save
        assert s._rate_storage_error
        s._start_rate_save(force=True)
        await s._rate_save
        assert "private-storage-detail" not in caplog.text
        assert caplog.text.count("could not be saved") == 1
        monkeypatch.setattr(s._rate_store, "save", original)
        s._start_rate_save(force=True)
        await s._rate_save
        assert not s._rate_storage_error
        s._rate_store = None
        last = s._rate_save
        s._start_rate_save(force=True)
        assert s._rate_save is last
    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_bus_stop_precedes_optional_flush_even_after_parent_failure(tmp_path, monkeypatch, failure):
    async def run():
        s = bare(tmp_path)
        stopped = []
        original = s._rate_store.save

        async def stop(_self):
            stopped.append(True)
            if failure:
                raise OSError("fixture parent stop")

        def save(raw):
            assert stopped
            original(raw)

        monkeypatch.setattr(m.WeeklyModbusService, "stop", stop)
        monkeypatch.setattr(s._rate_store, "save", save)
        if failure:
            with pytest.raises(OSError, match="fixture parent stop"):
                await s.stop()
        else:
            await s.stop()
        assert s._rate_save.done()
        assert s._rate_store.load().snapshot() == s.rates.history.snapshot()
        assert s._forecast_closing
    asyncio.run(run())


def test_cancelled_shutdown_joins_the_single_history_worker(tmp_path, monkeypatch):
    async def run():
        s = bare(tmp_path)
        started, release = threading.Event(), threading.Event()
        original = s._rate_store.save

        def save(raw):
            started.set()
            assert release.wait(5)
            original(raw)

        monkeypatch.setattr(s._rate_store, "save", save)
        monkeypatch.setattr(m.WeeklyModbusService, "stop", AsyncMock())
        s._start_rate_save()
        assert await asyncio.to_thread(started.wait, 5)
        task = asyncio.create_task(s.stop())
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s._rate_save.done()
        assert s._rate_store.load().snapshot() == s.rates.history.snapshot()
    asyncio.run(run())


def test_history_is_never_assigned_to_a_guest_or_an_old_connection(tmp_path):
    s = bare(tmp_path)
    s.weekly = None
    s.control = SimpleNamespace(soc_goal=SimpleNamespace(source="private-source"))
    order = SimpleNamespace(kind="scheduled", scope_ended=False, binding=SCOPE, identifier="fixture")
    expected = hashlib.sha256(b"source:private-source").hexdigest()
    assert s._rate_profile(order, SCOPE) == expected
    assert s._rate_profile(None, SCOPE) is None
    assert s._rate_profile(order, None) is None
    order.kind = "guest"
    assert s._rate_profile(order, SCOPE) is None
    order.kind, order.scope_ended = "scheduled", True
    assert s._rate_profile(order, SCOPE) is None
    order.scope_ended = False
    s.weekly = SimpleNamespace(consent=SimpleNamespace(profile="fixture-profile", job_id="fixture"))
    assert s._rate_profile(order, SCOPE) == hashlib.sha256(b"weekly:fixture-profile").hexdigest()
    s.weekly.consent.job_id = "other"
    assert s._rate_profile(order, SCOPE) == expected
    s.control = None
    assert s._rate_profile(order, SCOPE) is None


@pytest.mark.parametrize(("value", "expected"), [(None, 0), (True, 0), ("3", 0), (0, 0), (1, 1), (2.0, 2), (3, 3)])
def test_unknown_phase_count_is_not_presented_as_three_phase(tmp_path, value, expected):
    s = bare(tmp_path)
    s.data = WallboxData(values={"detected_ev_phases": value})
    assert s._rate_phases() == expected


@pytest.mark.parametrize("error", [OSError("fixture"), asyncio.CancelledError()])
def test_failed_refresh_discards_live_prediction_without_swallowing_failure(tmp_path, monkeypatch, error):
    async def run():
        s = bare(tmp_path)
        monkeypatch.setattr(m.WeeklyModbusService, "refresh", AsyncMock(side_effect=error))
        with pytest.raises(type(error)):
            await s.refresh()
        assert s.rates.reason == "unavailable"
    asyncio.run(run())


def test_passive_non_meter_refresh_and_unconfigured_advisory_callback(tmp_path, monkeypatch):
    async def run():
        s = bare(tmp_path)
        s.jobs = None
        s.data = WallboxData(values={"evse_state": 5, "detected_ev_phases": 3})
        s._job_inputs = lambda: sample()
        monkeypatch.setattr(m.WeeklyModbusService, "refresh", AsyncMock())
        before = s.rates.history.generation
        await s.refresh()
        if s._rate_save is not None:
            await s._rate_save
        assert s.rates.history.generation == before
        monkeypatch.setattr(m.WeeklyModbusService, "_configure_control", Mock())
        with pytest.raises(RuntimeError, match="job controller"):
            s._configure_control(None)
        monkeypatch.setattr(m.WeeklyChargeJobs, "snapshot", lambda _self: {"ordinary": True})
        controller = m.ForecastJobs.__new__(m.ForecastJobs)
        controller.job = None
        assert controller.snapshot() == {"ordinary": True}
        controller.forecast = lambda _job: {"state": "no_job"}
        assert controller.snapshot() == {"ordinary": True, "forecast": {"state": "no_job"}}
    asyncio.run(run())


def test_empty_identity_fallback_does_not_require_a_raw_serial(tmp_path, monkeypatch):
    async def run():
        s = bare(tmp_path)
        s.state_path = tmp_path / "intent.json"
        s.identity = SimpleNamespace(serial_number=None)
        s.config = SimpleNamespace(connection=SimpleNamespace(port="fixture-port", device_id=50))
        monkeypatch.setattr(m.WeeklyModbusService, "_initialize", AsyncMock())
        await s._initialize()
        assert s._rate_store.identity == "fixture-port:50"
        assert s.rates.history.snapshot() == []
    asyncio.run(run())
