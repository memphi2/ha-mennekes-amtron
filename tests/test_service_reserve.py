"""Exercise reserve preview and the real service's existing current-write path."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device.service_job_http import (
    create_job_app,
)
from tests import test_service_charge_runtime as runtime


def test_reserve_then_schedule_uses_one_baseline_and_only_current_writes(tmp_path):
    async def run():
        async with runtime.job_service(tmp_path) as (service, transport, clock):
            jobs = service.jobs
            raw = {**runtime.request(future=True), "reserve_kwh": 2}
            before = list(transport.reads)
            p = jobs.preview(raw, revision=jobs.revision, scope=jobs.snapshot()["scope"])
            assert transport.reads == before
            accepted = await jobs.create(p["request"], revision=p["revision"], scope=p["scope"])
            assert accepted["job"]["reserve_kwh"] == 2
            assert runtime.current_writes(transport)[-1] == 12
            baseline = jobs.job.energy.baseline_kwh
            clock[0] += 1000
            runtime.put(transport.bank, runtime.R.ENERGY_TOTAL, baseline + 2)
            service._poller._slow_read_at = None
            await service.refresh()
            await jobs.tick()
            assert jobs.job.reserve.reached
            assert runtime.current_writes(transport)[-1] == 1
            assert jobs.job.state == "scheduled"
            original = jobs.inputs
            jobs.inputs = lambda: replace(original(), now=datetime.now(UTC) + timedelta(hours=2))
            clock[0] += 1000
            await jobs.tick()
            assert runtime.current_writes(transport)[-1] == 12
            assert jobs.job.energy.baseline_kwh == baseline
            clock[0] += 1000
            runtime.put(transport.bank, runtime.R.ENERGY_TOTAL, baseline + 10)
            service._poller._slow_read_at = None
            await service.refresh()
            await jobs.tick()
            assert jobs.job.state == "reached"
            assert runtime.current_writes(transport)[-1] == 1
            assert all(address == runtime.R.CHARGING_CURRENT_EMS.address for address, _words in transport.writes)
    asyncio.run(run())


def test_authenticated_reserve_preview_confirmation_and_validation(tmp_path):
    async def run():
        async with runtime.job_service(tmp_path) as (service, transport, _clock):
            headers = {"Authorization": f"Bearer {runtime.TOKEN}"}
            async with TestClient(TestServer(create_job_app(service))) as client:
                assert (await client.get("/v2/jobs")).status == 401
                snapshot = await (await client.get("/v2/jobs", headers=headers)).json()
                request = {**runtime.request(future=True), "reserve_kwh": 2}
                payload = {"action": "preview", "revision": snapshot["revision"], "scope": snapshot["scope"], "request": request}
                before = list(transport.writes)
                invalid = await client.post("/v2/jobs", headers=headers, json={**payload, "request": {**request, "reserve_kwh": 11}})
                assert invalid.status == 400
                assert (await invalid.json())["error"] == "invalid_job_request"
                assert transport.writes == before
                result = await client.post("/v2/jobs", headers=headers, json=payload)
                assert result.status == 200
                preview = await result.json()
                assert preview["request"]["reserve_kwh"] == 2
                assert transport.writes == before
                payload = {"action": "create", "revision": preview["revision"], "scope": preview["scope"], "request": preview["request"]}
                created = await client.post("/v2/jobs", headers=headers, json=payload)
                assert created.status == 200
                assert (await created.json())["job"]["reserve_reached"] is False
                before = list(transport.writes)
                assert (await client.post("/v2/jobs", headers=headers, json=payload)).status == 200
                assert transport.writes == before
    asyncio.run(run())
