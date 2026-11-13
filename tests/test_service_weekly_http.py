"""Both weekly interfaces retain authentication, bounded input and confirmation."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings
from custom_components.mennekes_amtron_rtu.device.service_weekly_http import (
    WEEKLY_ASSETS,
    WeeklyConsole,
    create_weekly_app,
    create_weekly_console,
    weekly_controller,
    weekly_operation,
)
from custom_components.mennekes_amtron_rtu.device.weekly_controller import WeeklyError
from tests.test_weekly_controller import PLAN, SCOPE, Harness, recipe

TOKEN = "weekly-api-private-test-credential"  # noqa: S105 - fixture
HEADERS = {"X-Remote-User-Id": "f" * 32, "X-Remote-User-Groups": "system-admin"}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Console(WeeklyConsole):
    async def _request(self, method, path, payload=None):
        assert method == "GET"
        assert path == "/info"
        return {"supervisor": "2026.10.4"}


def service(h):
    return SimpleNamespace(weekly=h.weekly, jobs=h.jobs, config=SimpleNamespace(token=TOKEN))


@pytest.mark.parametrize("payload", [None, [], {}, {"action": "write_register"},
    {"action": "save_plan", "revision": "r", "plan": {}, "confirm": False},
    {"action": "activate", "proposal": {}, "confirm": True, "force": True}])
def test_weekly_action_allowlist(tmp_path, payload):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        with pytest.raises(WeeklyError, match="invalid_weekly_request"):
            await weekly_operation(h.weekly, payload)
        assert h.calls == []
    asyncio.run(run())


def test_unconfigured_weekly_service_is_not_an_empty_recipe_book():
    for value in (None, SimpleNamespace(weekly=None)):
        with pytest.raises(WeeklyError, match="weekly_service_starting"):
            weekly_controller(value)


@pytest.mark.parametrize("ingress", [False, True])
def test_authenticated_preview_save_activate_stop_and_remove(tmp_path, monkeypatch, ingress):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        s = service(h)
        settings = Console("supervisor-fixture", TOKEN, service=s)
        path = "/api/weekly" if ingress else "/v2/weekly"
        app = create_weekly_console(settings) if ingress else create_weekly_app(s)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get(path, headers=HEADERS if ingress else {})).status == (403 if ingress else 401)
            monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
            headers = HEADERS if ingress else AUTH
            response = await client.get(path, headers=headers)
            initial = await response.json()
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert TOKEN not in json.dumps(initial)
            if ingress:
                assert (await client.post(path, headers=headers, json={})).status == 403
                headers = {**headers, "X-Amtron-CSRF": initial["csrf"]}
            p = {"action": "preview_plan", "revision": initial["revision"], "plan": recipe()}
            preview = await (await client.post(path, headers=headers, json=p)).json()
            assert h.calls == []
            assert not h.weekly.store.path.exists()
            response = await client.post(path, headers=headers, json={
                "action": "save_plan", "revision": preview["revision"], "plan": preview["plan"], "confirm": True,
            })
            assert response.status == 200
            saved = await response.json()
            activation = await (await client.post(path, headers=headers, json={
                "action": "preview_activation", "revision": saved["revision"],
                "plan_id": PLAN, "vehicle_profile": "fixture", "scope": SCOPE,
            })).json()
            assert h.calls == []
            response = await client.post(path, headers=headers, json={"action": "activate", "proposal": activation, "confirm": True})
            assert response.status == 200
            accepted = await response.json()
            assert accepted["active"]["plan_id"] == PLAN
            stopped = await (await client.post(path, headers=headers, json={"action": "deactivate", "revision": accepted["revision"], "confirm": True})).json()
            assert stopped["job"]["state"] == "stopped"
            removed = await (await client.post(path, headers=headers, json={"action": "remove_plan", "plan_id": PLAN, "revision": stopped["revision"], "confirm": True})).json()
            assert removed["plans"] == []
            assert (await client.post(path, headers=headers, json={})).status == 409
            assert (await client.get(path + "?refresh=1", headers=headers)).status == (400 if ingress else 409)
            assert (await client.post(path + "?force=1", headers=headers, json={})).status == (400 if ingress else 409)
            assert (await client.post(path, headers=headers, data="text")).status == 415
            bad = {"action": "preview_plan", "revision": h.weekly.revision, "plan": {}}
            assert (await client.post(path, headers=headers, json=bad)).status == 400
            assert (await client.post(path, headers={**headers, "Content-Type": "application/json"}, data="{")).status == 400
            assert (await client.post(path, headers=headers, json={"x": "x" * 5000})).status == 413
            if ingress:
                for route, (_name, mime) in WEEKLY_ASSETS.items():
                    asset = await client.get(route, headers=headers)
                    assert asset.status == 200
                    assert asset.content_type == mime
                    assert await asset.read()
                settings.weekly_service = None
            else:
                s.weekly = None
            assert (await client.get(path, headers=headers)).status == (503 if ingress else 409)
            assert (await client.post(path, headers=headers, json=bad)).status == 409
    asyncio.run(run())


def test_weekly_api_backpressure_is_bounded(tmp_path, monkeypatch):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        entered, finish = asyncio.Event(), asyncio.Event()
        count = 0

        async def deactivate(**_kwargs):
            nonlocal count
            count += 1
            if count == 4:
                entered.set()
            await finish.wait()
            return h.weekly.snapshot()

        monkeypatch.setattr(h.weekly, "deactivate", deactivate)
        async with TestClient(TestServer(create_weekly_app(service(h)))) as client:
            p = {"action": "deactivate", "revision": h.weekly.revision, "confirm": True}
            tasks = [asyncio.create_task(client.post("/v2/weekly", headers=AUTH, json=p)) for _ in range(4)]
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert (await client.post("/v2/weekly", headers=AUTH, json=p)).status == 503
            finally:
                finish.set()
            assert all(response.status == 200 for response in await asyncio.gather(*tasks))
    asyncio.run(run())


@pytest.mark.parametrize("ingress", [False, True])
def test_unexpected_input_failures_do_not_expose_internal_details(tmp_path, monkeypatch, ingress):
    async def run():
        h = Harness(tmp_path)
        await h.initialize()
        monkeypatch.setattr(h.weekly, "save_plan", AsyncMock(side_effect=TypeError("private failure")))
        s = service(h)
        settings = Console("supervisor-fixture", TOKEN, service=s)
        monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        app = create_weekly_console(settings) if ingress else create_weekly_app(s)
        path = "/api/weekly" if ingress else "/v2/weekly"
        async with TestClient(TestServer(app)) as client:
            headers = dict(HEADERS if ingress else AUTH)
            initial = await (await client.get(path, headers=headers)).json()
            if ingress:
                headers["X-Amtron-CSRF"] = initial["csrf"]
            response = await client.post(path, headers=headers, json={"action": "save_plan", "plan": {}, "revision": h.weekly.revision, "confirm": True})
            assert response.status == 400
            assert "private" not in await response.text()
    asyncio.run(run())
