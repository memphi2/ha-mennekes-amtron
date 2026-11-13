"""Guest presets use exactly the existing authenticated job interfaces."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings
from custom_components.mennekes_amtron_rtu.device.service_job_http import (
    create_job_app,
    create_job_console,
)
from tests.test_guest_charge import Harness, request
from tests.test_service_charge_http import AUTH, HEADERS, TOKEN, Console, service


@pytest.mark.parametrize("ingress", [False, True])
def test_guest_preview_confirm_and_target_conflicts_use_existing_security(tmp_path, monkeypatch, ingress):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        settings = Console("supervisor-fixture", TOKEN, service=service(c))
        app = create_job_console(settings) if ingress else create_job_app(service(c))
        endpoint = "/api/jobs" if ingress else "/v2/jobs"
        if ingress:
            monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
        async with TestClient(TestServer(app)) as client:
            assert (await client.get(endpoint)).status == (403 if ingress else 401)
            headers = HEADERS if ingress else AUTH
            state = await (await client.get(endpoint, headers=headers)).json()
            payload = {"action": "preview_guest", "revision": state["revision"], "scope": state["scope"], "request": request()}
            assert (await client.post(endpoint, json=payload)).status == (403 if ingress else 401)
            if ingress:
                assert (await client.post(endpoint, headers=headers, json=payload)).status == 403
                headers = {**headers, "X-Amtron-CSRF": state["csrf"]}
                for path in ("/guest", "/app_guest.js"):
                    response = await client.get(path, headers=HEADERS)
                    assert response.status == 200
                    assert TOKEN not in await response.text()
            response = await client.post(endpoint, headers=headers, json=payload)
            assert response.status == 200
            preview = await response.json()
            assert preview["request"]["kind"] == "guest"
            assert h.calls == []
            assert not h.store.path.exists()
            h.change(vehicle_target_active=True)
            confirm = {**payload, "action": "create", "request": preview["request"]}
            response = await client.post(endpoint, headers=headers, json=confirm)
            assert response.status == 409
            assert (await response.json())["error"] == "guest_vehicle_target_active"
            assert h.calls == []
            h.change(vehicle_target_active=False)
            response = await client.post(endpoint, headers=headers, json=confirm)
            assert response.status == 200
            accepted = await response.json()
            assert accepted["job"]["kind"] == "guest"
            assert accepted["job"]["energy_kwh"] == 15
            assert h.calls == [12]
            # An extra field is refused by the action allowlist itself, with a
            # current revision, before any job or permission check.
            current = await (await client.get(endpoint, headers=HEADERS if ingress else AUTH)).json()
            smuggled = {**payload, "revision": current["revision"], "scope": current["scope"], "allow_rfid_bypass": True}
            response = await client.post(endpoint, headers=headers, json=smuggled)
            assert response.status == 409
            assert (await response.json())["error"] == "invalid_job_request"
            await c.stop()
    asyncio.run(run())
