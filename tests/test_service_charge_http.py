"""Job APIs keep the existing authentication, CSRF and bounded command surface."""

from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device.service_job_http import (
    JobConsole,
    create_job_app,
)

TOKEN = "job-api-test-private-token"  # noqa: S105 - fixture
HEADERS = {"X-Remote-User-Id": "f" * 32, "X-Remote-User-Groups": "system-admin"}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Console(JobConsole):
    async def _request(self, method, path, payload=None):
        assert method == "GET"
        assert path == "/info"
        return {"supervisor": "2026.10.4"}



def test_obsolete_order_routes_are_not_exposed(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
        DepartureModbusService,
    )
    from tests import test_service_charge_runtime as runtime
    async def run():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, _transport, _clock):
            service.config = __import__("dataclasses").replace(service.config, token=TOKEN)
            async with TestClient(TestServer(create_job_app(service))) as client:
                assert (await client.post("/v2/jobs", headers=AUTH, json={"action":"create"})).status == 404
    asyncio.run(run())
