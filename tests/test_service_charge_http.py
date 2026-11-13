"""Job APIs keep the existing authentication, CSRF and bounded command surface."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from custom_components.mennekes_amtron_rtu.device.service_job_http import (
    JobConsole,
    create_job_app,
    create_job_console,
    job_controller,
    job_operation,
)
from tests.test_service_charge_controller import Harness

TOKEN = "job-api-test-private-token"  # noqa: S105 - fixture
HEADERS = {"X-Remote-User-Id": "f" * 32, "X-Remote-User-Groups": "system-admin"}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Console(JobConsole):
    async def _request(self, method, path, payload=None):
        assert method == "GET"
        assert path == "/info"
        return {"supervisor": "2026.10.4"}


def service(controller):
    return SimpleNamespace(jobs=controller, config=SimpleNamespace(token=TOKEN))


@pytest.mark.parametrize("payload", [None, [], {}, {"action": "write_register"}, {"action": "stop", "revision": "r", "confirm": False}, {"action": "clear", "revision": "r", "confirm": True, "address": 1}])
def test_action_allowlist_rejects_unsupported_payloads(tmp_path, payload):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        with pytest.raises(JobRequestError, match="invalid_job_request"):
            await job_operation(h.controller, payload)
        assert h.calls == []
    asyncio.run(run())


def test_unconfigured_controller_is_not_a_new_permission():
    for value in (None, service(None)):
        with pytest.raises(JobRequestError, match="job_service_starting"):
            job_controller(value)


def test_bearer_api_preview_create_stop_clear_and_rejected_requests(tmp_path):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        s = service(c)
        async with TestClient(TestServer(create_job_app(s))) as client:
            assert (await client.get("/v2/jobs")).status == 401
            assert (await client.get("/v2/jobs?refresh=1", headers=AUTH)).status == 409
            response = await client.get("/v2/jobs", headers=AUTH)
            initial = await response.json()
            assert response.headers["Cache-Control"] == "no-store"
            assert TOKEN not in json.dumps(initial)
            assert (await client.post("/v2/jobs", headers=AUTH, data="text")).status == 415
            assert (await client.post("/v2/jobs", headers={**AUTH, "Content-Type": "application/json"}, data="{" )).status == 400
            payload = {"action": "preview", "revision": initial["revision"], "scope": initial["scope"], "request": h.request()}
            preview = await (await client.post("/v2/jobs", headers=AUTH, json=payload)).json()
            assert h.calls == []
            assert not h.store.path.exists()
            payload.update(action="create", request=preview["request"])
            response = await client.post("/v2/jobs", headers=AUTH, json=payload)
            assert response.status == 200
            accepted = await response.json()
            assert accepted["job"]["state"] == "charging"
            stopped = await (await client.post("/v2/jobs", headers=AUTH, json={"action": "stop", "revision": accepted["revision"], "confirm": True})).json()
            assert stopped["job"]["state"] == "stopped"
            cleared = await (await client.post("/v2/jobs", headers=AUTH, json={"action": "clear", "revision": stopped["revision"], "confirm": True})).json()
            assert cleared["job"] is None
            bad = {"action": "create", "revision": cleared["revision"], "scope": initial["scope"], "request": {}}
            assert (await client.post("/v2/jobs", headers=AUTH, json=bad)).status == 400
            s.jobs = None
            assert (await client.get("/v2/jobs", headers=AUTH)).status == 409
    asyncio.run(run())


def test_api_backpressure_does_not_start_unbounded_command_tasks(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        entered, finish = asyncio.Event(), asyncio.Event()
        count = 0
        async def stop(**_kwargs):
            nonlocal count
            count += 1
            if count == 4:
                entered.set()
            await finish.wait()
            return h.controller.snapshot()
        monkeypatch.setattr(h.controller, "stop", stop)
        async with TestClient(TestServer(create_job_app(service(h.controller)))) as client:
            payload = {"action": "stop", "revision": h.controller.revision, "confirm": True}
            tasks = [asyncio.create_task(client.post("/v2/jobs", headers=AUTH, json=payload)) for _ in range(4)]
            await asyncio.wait_for(entered.wait(), 2)
            assert (await client.post("/v2/jobs", headers=AUTH, json=payload)).status == 503
            finish.set()
            assert all(response.status == 200 for response in await asyncio.gather(*tasks))
    asyncio.run(run())


def test_ingress_checks_peer_admin_csrf_and_does_not_expose_credentials(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        settings = Console("supervisor-fixture", TOKEN, service=service(h.controller))
        async with TestClient(TestServer(create_job_console(settings))) as client:
            assert (await client.get("/api/jobs", headers=HEADERS)).status == 403
            monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
            assert (await client.get("/api/jobs")).status == 403
            assert (await client.get("/api/jobs?refresh=1", headers=HEADERS)).status == 400
            response = await client.get("/api/jobs", headers=HEADERS)
            initial = await response.json()
            assert response.headers["Cache-Control"] == "no-store"
            assert TOKEN not in json.dumps(initial)
            headers = {**HEADERS, "X-Amtron-CSRF": initial["csrf"]}
            assert (await client.post("/api/jobs", headers=HEADERS, json={})).status == 403
            assert (await client.post("/api/jobs?force=1", headers=headers, json={})).status == 400
            assert (await client.post("/api/jobs", headers=headers, json={})).status == 409
            payload = {"action": "create", "revision": initial["revision"], "scope": initial["scope"], "request": {}}
            assert (await client.post("/api/jobs", headers=headers, json=payload)).status == 400
            payload["request"] = h.request()
            assert (await client.post("/api/jobs", headers=headers, json=payload)).status == 200
            settings.job_service = None
            assert (await client.get("/api/jobs", headers=HEADERS)).status == 503
            assert (await client.post("/api/jobs", headers=headers, json=payload)).status == 409
    asyncio.run(run())


def test_invalid_command_type_does_not_leak_an_exception(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.controller.initialize()
        monkeypatch.setattr(h.controller, "create", AsyncMock(side_effect=TypeError("private failure")))
        payload = {"action": "create", "revision": h.controller.revision, "scope": h.sample.scope, "request": h.request()}
        async with TestClient(TestServer(create_job_app(service(h.controller)))) as client:
            response = await client.post("/v2/jobs", headers=AUTH, json=payload)
            assert response.status == 400
            assert "private" not in await response.text()
    asyncio.run(run())
