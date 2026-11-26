"""Runtime settings reuse the admin gate and never bypass saved configuration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ServerDisconnectedError
from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.console_http import create_console_app
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
    DeviceValidationError,
)
from custom_components.mennekes_amtron_rtu.device.service_api import ServiceApi
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_transport import EmbeddedTransport
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


def test_one_save_applies_every_runtime_setting_without_bus_side_effects(tmp_path, monkeypatch):
    async def run():
        service, transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            async with TestClient(TestServer(create_console_app(settings))) as client:
                headers, revision = await edit_context(client)
                changes = {
                    "scan_interval": 11, "measurement_interval": 45, "current_cap": 8,
                    "state_change_interval_seconds": 60, "phase_switch_grace_seconds": 10, "pause_before_phase_switch": False,
                }
                before = (list(transport.reads), list(transport.writes))
                owner, heartbeat, poller, control = service.client, service.heartbeat, service._poller, service.control
                retry_history = deepcopy(poller._block_retries)
                read_history = deepcopy(poller._block_read_at)
                result = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": changes})
                assert result.status == 200
                data = await result.json()
                assert data["restart_fields"] == []
                # What this save changed, with when each change takes effect.
                assert {change["key"]: change["effect"] for change in data["changes"]} == {
                    "scan_interval": "next_cycle", "measurement_interval": "next_cycle", "current_cap": "immediate",
                    "state_change_interval_seconds": "immediate", "phase_switch_grace_seconds": "next_switch", "pause_before_phase_switch": "next_switch",
                }
                assert data["saved_ok"]
                assert data["active"] == data["saved"] == changes
                assert control.options.pause_before_phase_switch is False
                assert not data["restart_required"]
                assert service.config.scan_interval == 11
                assert service.config.measurement_interval == 45
                assert service.config.current_cap == 8
                assert service.control is control
                assert control.max_charging_current == 8
                assert control.options.state_change_interval_seconds == 60
                assert control.options.phase_switch_grace_seconds == 10
                assert control.read_only
                published = service.snapshot()["control"]
                assert (published["current_cap"], published["max_charging_current"], published["state_change_interval_seconds"]) == (8, 8, 60)
                assert settings.options["current_cap"] == 8
                assert settings.options["read_only"] is True
                assert service.client is owner
                assert service.heartbeat is heartbeat
                assert service._poller is poller
                assert poller._block_retries == retry_history
                assert poller._block_read_at == read_history
                assert (transport.reads, transport.writes) == before
                assert (await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": changes})).status == 409
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
                payload = {"revision": revision, "changes": {"scan_interval": 10}}
                assert (await client.post("/api/runtime", headers=HEADERS, json=payload)).status == 403
                assert (await client.get("/api/runtime?force=1", headers=HEADERS)).status == 400
                assert (await client.post("/api/runtime?force=1", headers=headers, json=payload)).status == 400
                for bad in (
                    {}, {**payload, "confirm": True}, {**payload, "extra": True}, {**payload, "revision": None},
                    {**payload, "changes": {"token": "not-permitted"}}, {**payload, "changes": {"scan_interval": 0}},
                ):
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
            original = service.apply_runtime_settings
            monkeypatch.setattr(service, "apply_runtime_settings", AsyncMock(side_effect=failure))
            async with TestClient(TestServer(create_console_app(settings))) as client:
                headers, revision = await edit_context(client)
                response = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": {"scan_interval": 10}})
                assert response.status == 503
                assert (await response.json())["error"] == "runtime_apply_pending"
                result = await (await client.get("/api/runtime", headers=HEADERS)).json()
                assert result["active"]["scan_interval"] == 5
                assert result["saved"]["scan_interval"] == 10
                assert result["revision"] != revision
                monkeypatch.setattr(service, "apply_runtime_settings", original)
                retry = await client.post("/api/runtime", headers=headers, json={"revision": result["revision"], "changes": {"scan_interval": 10}})
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
                            settings.options["state_change_interval_seconds"] = 400
                    return await original(method, path, payload)

                monkeypatch.setattr(settings, "_request", request)
                result = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": {"scan_interval": 10}})
                assert result.status == 409
                assert settings.options["state_change_interval_seconds"] == 400
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
                result = await client.post("/api/runtime", headers=headers, json={"revision": revision, "changes": {"scan_interval": 10}})
                assert result.status == (200 if applied else 503)
                assert len(posts) == 1
                assert service.config.scan_interval == (10 if applied else 5)
                assert settings._options_write_uncertain is not applied
        finally:
            await service.stop()
    asyncio.run(run())


def test_runtime_update_preserves_cadence_and_refuses_an_unready_or_stopping_service(tmp_path):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            poller, control = service._poller, service.control
            for invalid in (None, True, 1, 301, float("nan"), "5"):
                with pytest.raises(ValueError, match="measurement interval"):
                    poller.update_measurement_interval(invalid)
                with pytest.raises(ValueError, match="numeric value"):
                    await service.apply_runtime_settings(replace(service.config, measurement_interval=invalid))
            with pytest.raises(ValueError, match="numeric value"):
                await service.apply_runtime_settings(replace(service.config, current_cap=5))
            assert service.config.current_cap == 16
            assert control.max_charging_current == 16
            service._poller = None
            await service.apply_runtime_settings(replace(service.config, scan_interval=7, measurement_interval=11))
            assert service.config.measurement_interval == 11
            service._poller = poller
            # Before the first connection there is no control to update yet.
            service.control = None
            with pytest.raises(DeviceError):
                await service.apply_runtime_settings(replace(service.config, current_cap=10))
            service.control = control
            assert service.config.current_cap == 16
        finally:
            await service.stop()
        with pytest.raises(DeviceError):
            await service.apply_runtime_settings(replace(service.config, scan_interval=7))
    asyncio.run(run())


def test_home_assistant_saves_only_its_fields_through_the_same_store(tmp_path, monkeypatch):
    async def run():
        service, transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            api = ServiceApi(service)
            # A standalone service has nowhere to keep a change.
            status, body = await api.dispatch("POST", "/v1/settings", {"changes": {"pause_before_phase_switch": False}})
            assert (status, body["error"], body["validation"]) == (409, "settings_not_writable", True)
            assert service.snapshot()["control"]["settings_writable"] is False
            service.settings_writer = settings
            assert service.snapshot()["control"]["settings_writable"] is True
            settings.options["scan_interval"] = 7  # saved on the Configuration tab, not applied yet
            before = (list(transport.reads), list(transport.writes))
            status, body = await api.dispatch("POST", "/v1/settings", {"changes": {
                "pause_before_phase_switch": False, "phase_switch_grace_seconds": 12,
            }})
            assert (status, body) == (200, {"saved": True})
            control = service.snapshot()["control"]
            assert (control["pause_before_phase_switch"], control["phase_switch_grace_seconds"]) == (False, 12)
            assert service.control.options.pause_before_phase_switch is False
            assert (settings.options["pause_before_phase_switch"], settings.options["phase_switch_grace_seconds"]) == (False, 12)
            # The other stored values stay, and one saved elsewhere keeps waiting.
            assert settings.options["current_cap"] == 16
            assert settings.options["scan_interval"] == 7
            assert service.config.scan_interval == 5
            assert (transport.reads, transport.writes) == before
            # The cap and the write interval stay with an administrator.
            for other in ({"current_cap": 32}, {"state_change_interval_seconds": 0}, {"pause_before_phase_switch": True, "scan_interval": 2}, {}, []):
                status, body = await api.dispatch("POST", "/v1/settings", {"changes": other})
                assert (status, body["error"], body["validation"]) == (409, "runtime_settings_invalid", True)
            assert settings.options["current_cap"] == 16
            for bad in ({}, {"changes": {}, "extra": 1}, None):
                assert (await api.dispatch("POST", "/v1/settings", bad))[0] == 400
            status, body = await api.dispatch("POST", "/v1/settings", {"changes": {"phase_switch_grace_seconds": 31}})
            assert (status, body["error"], body["validation"]) == (409, "runtime_settings_invalid", True)
            with pytest.raises(DeviceValidationError):
                await settings.save_runtime_settings({"token": "not-permitted"})
            # Another save holds the lock: HA does not wait for it.
            async with settings._lock:
                status, body = await api.dispatch("POST", "/v1/settings", {"changes": {"pause_before_phase_switch": True}})
            assert (status, body["error"], body["validation"]) == (409, "busy", False)
            original = settings._request

            for failure in (ServerDisconnectedError(), TimeoutError()):
                async def unreachable(method, path, payload=None, failure=failure):
                    raise failure

                settings._request = unreachable
                status, body = await api.dispatch("POST", "/v1/settings", {"changes": {"pause_before_phase_switch": True}})
                assert (status, body["error"]) == (409, "supervisor_unavailable")
            settings._request = original
            settings._options_write_uncertain = True
            status, body = await api.dispatch("POST", "/v1/settings", {"changes": {"pause_before_phase_switch": True}})
            assert (status, body["error"], body["validation"]) == (409, "options_save_uncertain", False)
            assert settings.options["pause_before_phase_switch"] is False
        finally:
            await service.stop()
    asyncio.run(run())


def test_home_assistant_client_saves_through_the_app_and_reports_failures(tmp_path):
    async def run():
        service, _transport = await console_fixture(tmp_path)
        try:
            settings = settings_for(service)
            service.settings_writer = settings
            remote = ServiceClient(transport=EmbeddedTransport(ServiceApi(service)))
            with pytest.raises(DeviceError, match="service_unavailable"):
                await remote.async_save_settings({"pause_before_phase_switch": False})
            await remote.async_connect()
            assert remote.snapshot["control"]["settings_writable"] is True
            await remote.async_save_settings({"pause_before_phase_switch": False})
            assert settings.options["pause_before_phase_switch"] is False
            # The next poll shows the value.
            await remote.update()
            assert remote.snapshot["control"]["pause_before_phase_switch"] is False
            with pytest.raises(DeviceValidationError, match="runtime_settings_invalid"):
                await remote.async_save_settings({"phase_switch_grace_seconds": 31})
            settings._options_write_uncertain = True
            with pytest.raises(DeviceError, match="options_save_uncertain"):
                await remote.async_save_settings({"pause_before_phase_switch": True})
            assert remote.connected
            # An unreachable app fails the save but leaves the entities alone.
            service.settings_writer = None
            with pytest.raises(DeviceValidationError, match="settings_not_writable"):
                await remote.async_save_settings({"pause_before_phase_switch": True})
            service.settings_writer = settings
            with pytest.raises(DeviceError, match="service_unavailable"):
                await ServiceClient(transport=_Unavailable()).async_save_settings({"pause_before_phase_switch": True})
            settings._options_write_uncertain = False
            remote._transport = _Unavailable()
            with pytest.raises(DeviceError, match="service_unavailable"):
                await remote.async_save_settings({"pause_before_phase_switch": True})
            assert remote.connected
        finally:
            await service.stop()
    asyncio.run(run())


class _Unavailable:
    async def call(self, method, path, payload):
        return 503, {"error": "service_unavailable"}
