"""Boost keeps the existing authenticated routes and guarded actuator."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings
from custom_components.mennekes_amtron_rtu.device.service_job_http import (
    create_job_app,
    create_job_console,
)
from tests.test_service_charge_controller import Harness
from tests.test_service_charge_http import AUTH, HEADERS, TOKEN, Console, service
from tests.test_service_charge_runtime import current_writes, job_service, request


@pytest.mark.parametrize("ingress", [False, True])
def test_boost_preview_confirm_cancel_use_existing_authentication(tmp_path, monkeypatch, ingress):
    async def run():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await h.create(delay=3600)
        s = service(c)
        if ingress:
            monkeypatch.setattr(app_settings, "SUPERVISOR_PEER", "127.0.0.1")
            app = create_job_console(Console("supervisor-fixture", TOKEN, service=s))
        else:
            app = create_job_app(s)
        url = "/api/jobs" if ingress else "/v2/jobs"
        headers = HEADERS if ingress else AUTH
        async with TestClient(TestServer(app)) as client:
            initial = await (await client.get(url, headers=headers)).json()
            edit_headers = {**headers, "X-Amtron-CSRF": initial["csrf"]} if ingress else headers
            payload = {"action": "preview_boost", "revision": initial["revision"], "scope": initial["scope"], "minutes": 30}
            before = list(h.calls)
            assert (await client.post(url, json=payload)).status == (403 if ingress else 401)
            if ingress:
                assert (await client.post(url, headers=HEADERS, json=payload)).status == 403
            response = await client.post(url, headers=edit_headers, json=payload)
            assert response.status == 200
            preview = await response.json()
            assert h.calls == before
            confirmation = {"action": "boost", "revision": preview["revision"], "scope": preview["scope"], "until": preview["until"]}
            assert (await client.post(url, headers=edit_headers, json={**confirmation, "address": 1})).status == 409
            response = await client.post(url, headers=edit_headers, json=confirmation)
            assert response.status == 200
            active = await response.json()
            assert active["job"]["boost_until"] == preview["until"]
            assert h.calls[-1][0] == 12
            before = list(h.calls)
            assert (await client.post(url, headers=edit_headers, json=confirmation)).status == 409
            assert h.calls == before
            response = await client.post(url, headers=edit_headers, json={"action": "cancel_boost", "revision": active["revision"], "confirm": True})
            assert response.status == 200
            cancelled = await response.json()
            assert cancelled["job"]["state"] == "scheduled"
            assert cancelled["job"]["boost_until"] is None
            confirmation.update(revision=cancelled["revision"], until="invalid")
            assert (await client.post(url, headers=edit_headers, json=confirmation)).status == 400
    asyncio.run(run())


def test_boost_expiry_cancels_start_permission_in_the_real_service(tmp_path):
    async def run():
        async with job_service(tmp_path) as (s, transport, clock):
            c = s.jobs
            await c.create(request(future=True), revision=c.revision, scope=c.snapshot()["scope"])
            clock[0] += 1000
            before = list(transport.reads)
            preview = c.preview_boost(30, revision=c.revision, scope=c.snapshot()["scope"])
            assert transport.reads == before
            await c.boost(preview["until"], revision=preview["revision"], scope=preview["scope"])
            assert current_writes(transport)[-1] == 12
            current_inputs = c.inputs
            expiry = c.job.boost_until + timedelta(seconds=1)
            c.inputs = lambda: replace(current_inputs(), now=expiry)
            assert c.current_policy("service-job:" + c.job.identifier, True) is False
            clock[0] += 1000
            await c.tick()
            assert c.job.state == "scheduled"
            assert current_writes(transport)[-1] == 1
    asyncio.run(run())
