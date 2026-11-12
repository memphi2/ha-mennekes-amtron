"""Runtime settings reuse the admin gate and never bypass saved configuration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.console_http import create_console_app
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from tests.test_app_settings import BASE, HEADERS
from tests.test_console_registers import FixtureConsole, console_fixture


def settings_for(service):
    settings = FixtureConsole(service)
    settings.options = {**BASE, "serial_port": "/dev/fake", "current_cap": 16}
    return settings


async def edit_context(client):
    response = await client.get("/api/runtime", headers=HEADERS)
    assert response.status == 200
    data = await response.json()
    return {**HEADERS, "X-Amtron-CSRF": data["csrf"]}, data["revision"]


def test_preview_and_save_apply_only_hot_intervals_without_bus_side_effects(tmp_path, monkeypatch):
    async def run():
        service, transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            async with TestClient(TestServer(create_console_app(settings))) as client:
                headers, revision = await edit_context(client)
                changes = {"scan_interval": 11.5, "measurement_interval": 45, "current_cap": 8}
                before = (list(transport.reads), list(transport.writes))
                owner, heartbeat, poller = service.client, service.heartbeat, service._poller
                retry_history = deepcopy(poller._block_retries)
                read_history = deepcopy(poller._block_read_at)
                response = await client.post("/api/runtime/preview", headers=headers, json={"revision": revision, "changes": changes})
                assert response.status == 200
                preview = await response.json()
                assert preview["restart_fields"] == ["current_cap"]
                assert service.config.scan_interval == 5
                assert not any(method == "POST" for method, _path, _payload in settings.calls)
                result = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": changes, "confirm": True})
                assert result.status == 200
                data = await result.json()
                assert data["saved_ok"]
                assert data["active"]["current_cap"] == 16
                assert data["saved"]["current_cap"] == 8
                assert data["restart_required"]
                assert service.config.scan_interval == 11.5
                assert service.config.measurement_interval == 45
                assert service.config.current_cap == 16
                assert settings.options["current_cap"] == 8
                assert settings.options["read_only"] is True
                assert service.client is owner
                assert service.heartbeat is heartbeat
                assert service._poller is poller
                assert poller._block_retries == retry_history
                assert poller._block_read_at == read_history
                assert (transport.reads, transport.writes) == before
                assert (await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": changes, "confirm": True})).status == 409
                for path in ("/runtime", "/app_runtime.js"):
                    assert (await client.get(path)).status == 200
        finally:
            await service.stop()
    asyncio.run(run())


def test_runtime_requires_real_peer_admin_csrf_and_exact_payload(tmp_path, monkeypatch):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            async with TestClient(TestServer(create_console_app(settings))) as client:
                assert (await client.get("/api/runtime", headers=HEADERS)).status == 403
                monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
                assert (await client.get("/api/runtime")).status == 403
                headers, revision = await edit_context(client)
                payload = {"revision": revision, "changes": {"scan_interval": 10}, "confirm": True}
                assert (await client.post("/api/runtime", headers=HEADERS, json=payload)).status == 403
                assert (await client.get("/api/runtime?force=1", headers=HEADERS)).status == 400
                assert (await client.post("/api/runtime?force=1", headers=headers, json=payload)).status == 400
                for bad in ({}, {**payload, "confirm": 1}, {**payload, "extra": True}, {**payload, "changes": {"token": "not-permitted"}}, {**payload, "changes": {"scan_interval": 0}}):
                    assert (await client.post("/api/runtime", headers=headers, json=bad)).status == 400
                settings._options_write_uncertain = True
                assert (await client.post("/api/runtime", headers=headers, json=payload)).status == 503
                assert (await (await client.get("/api/runtime", headers=HEADERS)).json())["write_blocked"]
                settings._options_write_uncertain = False
                settings.options["current_cap"] = 0
                assert (await client.get("/api/runtime", headers=HEADERS)).status == 400
                settings.service = None
                assert (await client.get("/api/runtime", headers=HEADERS)).status == 503
                assert not any(method == "POST" for method, _path, _payload in settings.calls)
        finally:
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("failure", [TimeoutError(), DeviceError("service_unavailable")])
def test_saved_but_not_applied_remains_visible_and_retry_requires_new_revision(tmp_path, monkeypatch, failure):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            original = service.apply_polling_intervals
            monkeypatch.setattr(service, "apply_polling_intervals", AsyncMock(side_effect=failure))
            async with TestClient(TestServer(create_console_app(settings))) as client:
                headers, revision = await edit_context(client)
                response = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": {"scan_interval": 10}, "confirm": True})
                assert response.status == 503
                assert (await response.json())["error"] == "runtime_apply_pending"
                result = await (await client.get("/api/runtime", headers=HEADERS)).json()
                assert result["active"]["scan_interval"] == 5
                assert result["saved"]["scan_interval"] == 10
                assert result["revision"] != revision
                monkeypatch.setattr(service, "apply_polling_intervals", original)
                retry = await client.post("/api/runtime", headers=headers, json={"revision": result["revision"], "changes": {"scan_interval": 10}, "confirm": True})
                assert retry.status == 200
                assert service.config.scan_interval == 10
                assert sum(method == "POST" for method, _path, _payload in settings.calls) == 1
        finally:
            await service.stop()
    asyncio.run(run())


def test_concurrent_native_settings_are_not_overwritten(tmp_path, monkeypatch):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            async with TestClient(TestServer(create_console_app(settings))) as client:
                headers, revision = await edit_context(client)
                original, reads = settings._request, 0

                async def request(method, path, payload=None):
                    nonlocal reads
                    if path == "/addons/self/info":
                        reads += 1
                        if reads == 2:
                            settings.options["port"] = 8888
                    return await original(method, path, payload)

                monkeypatch.setattr(settings, "_request", request)
                result = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": {"scan_interval": 10}, "confirm": True})
                assert result.status == 409
                assert settings.options["port"] == 8888
                assert settings.options["scan_interval"] == 5
                assert not any(method == "POST" for method, _path, _payload in settings.calls)
        finally:
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("applied", [False, True])
def test_lost_supervisor_reply_is_resolved_or_fenced_not_retried(tmp_path, monkeypatch, applied):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            async with TestClient(TestServer(create_console_app(settings))) as client:
                headers, revision = await edit_context(client)
                original, posts = settings._request, []

                async def request(method, path, payload=None):
                    if method == "POST":
                        posts.append(payload)
                        if applied:
                            await original(method, path, payload)
                        raise OSError("lost reply")
                    return await original(method, path, payload)

                monkeypatch.setattr(settings, "_request", request)
                result = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": {"scan_interval": 10}, "confirm": True})
                assert result.status == (200 if applied else 503)
                assert len(posts) == 1
                assert service.config.scan_interval == (10 if applied else 5)
                assert settings._options_write_uncertain is not applied
        finally:
            await service.stop()
    asyncio.run(run())


def test_polling_update_preserves_cadence_and_refuses_shutdown(tmp_path):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            poller = service._poller
            for invalid in (None, True, 1, 301, float("nan"), "5"):
                with pytest.raises(ValueError, match="measurement interval"):
                    poller.update_measurement_interval(invalid)
                with pytest.raises(ValueError, match="numeric value"):
                    await service.apply_polling_intervals(invalid, 30)
            service._poller = None
            await service.apply_polling_intervals(7, 11)
            assert service.config.measurement_interval == 11
            service._poller = poller
        finally:
            await service.stop()
        with pytest.raises(DeviceError):
            await service.apply_polling_intervals(7, 11)
    asyncio.run(run())
