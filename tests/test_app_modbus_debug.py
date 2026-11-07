"""Ingress-only live Modbus debugging reuses administrator and CSRF boundaries."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from custom_components.mennekes_amtron_rtu.device.app_settings import (
    create_settings_app,
)
from custom_components.mennekes_amtron_rtu.device.modbus_trace import ModbusTrace
from tests.test_app_settings import HEADERS, USER, FixtureSettings, body, request


async def invoke(settings, req):
    try:
        return await settings.guard(req, settings.set_modbus_debug if req.method == "POST" else settings.read_modbus_debug)
    except web.HTTPException as error:
        return error


def authorized(settings):
    return {**HEADERS, "X-Amtron-CSRF": settings._digest("csrf", USER)}


def test_explicit_switch_is_ram_only_and_clear_retains_deadline():
    async def check():
        settings = FixtureSettings()
        settings._modbus_trace = ModbusTrace()
        initial = await invoke(settings, request())
        assert body(initial)["enabled"] is False
        assert initial.headers["Cache-Control"] == "no-store"
        enabled = await invoke(settings, request("POST", headers=authorized(settings), payload={"enabled": True}))
        assert body(enabled)["enabled"] is True
        assert body(enabled)["expires_in"] == 600
        trace = settings._modbus_trace
        trace.finish(trace.start(), operation="read", address=0x0100, count=1, device_id=50, words=[1], error=None)
        assert len(body(await invoke(settings, request()))["records"]) == 1
        after = request().clone(rel_url="/api/modbus-debug?after=1")
        assert body(await invoke(settings, after))["records"] == []
        cleared = await invoke(settings, request("POST", headers=authorized(settings), payload={"clear": True}))
        assert body(cleared)["records"] == []
        assert body(cleared)["enabled"] is True
        disabled = await invoke(settings, request("POST", headers=authorized(settings), payload={"enabled": False}))
        assert body(disabled)["enabled"] is False
        assert all(call[1] == "/info" for call in settings.calls)
        assert "debug" not in settings.options
        routes = {(route.method, route.resource.canonical) for route in create_settings_app(settings).router.routes()}
        assert ("GET", "/api/modbus-debug") in routes
        assert ("POST", "/api/modbus-debug") in routes
    asyncio.run(check())


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("boundary", ["peer", "admin", "cross_site"])
def test_debug_uses_real_ingress_and_admin_guards(method, boundary):
    async def check():
        settings = FixtureSettings()
        settings._modbus_trace = ModbusTrace()
        headers = authorized(settings)
        kwargs = {"headers": headers}
        if method == "POST":
            kwargs["payload"] = {"enabled": True}
        if boundary == "peer":
            kwargs["peer"] = "127.0.0.1"
            headers["X-Forwarded-For"] = "172.30.32.2"
        elif boundary == "admin":
            headers["X-Remote-User-Groups"] = "system-users"
        else:
            headers["Sec-Fetch-Site"] = "cross-site"
        assert (await invoke(settings, request(method, **kwargs))).status == 403
        assert settings._modbus_trace.snapshot()["enabled"] is False
    asyncio.run(check())


def test_switch_requires_csrf_even_for_disable_or_clear():
    async def check():
        settings = FixtureSettings()
        settings._modbus_trace = ModbusTrace()
        for payload in ({"enabled": True}, {"enabled": False}, {"clear": True}):
            response = await invoke(settings, request("POST", payload=payload))
            assert response.status == 403
            assert body(response)["error"] == "reload_required"
    asyncio.run(check())


@pytest.mark.parametrize("payload", [{"enabled": 1}, {"enabled": "true"}, {"clear": 1}, {"clear": False}, {"enabled": True, "duration": 99999}, {"address": 0}, {}])
def test_debug_does_not_accept_commands_or_unbounded_capture_settings(payload):
    async def check():
        settings = FixtureSettings()
        settings._modbus_trace = ModbusTrace()
        result = await invoke(settings, request("POST", headers=authorized(settings), payload=payload))
        assert result.status == 400
        assert settings._modbus_trace.snapshot()["enabled"] is False
    asyncio.run(check())


def test_setup_only_mode_explains_why_debug_is_unavailable():
    async def check():
        settings = FixtureSettings()
        assert body(await invoke(settings, request()))["available"] is False
        result = await invoke(settings, request("POST", headers=authorized(settings), payload={"enabled": True}))
        assert result.status == 409
        assert body(result) == {"error": "debug_unavailable"}
    asyncio.run(check())


@pytest.mark.parametrize("query", ["after=-1", "after=abc", "after=" + "9" * 19, "token=secret"])
def test_debug_cursor_is_bounded_and_does_not_accept_other_parameters(query):
    async def check():
        settings = FixtureSettings()
        assert (await invoke(settings, request().clone(rel_url="/api/modbus-debug?" + query))).status == 400
    asyncio.run(check())
