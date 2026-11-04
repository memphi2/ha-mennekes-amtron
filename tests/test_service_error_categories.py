"""Connection diagnosis must not mistake valid hardware codes for bad tokens."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device.capabilities import (
    phase_switch_is_supported,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.test_modbus_service import TOKEN, service_fixture


@pytest.mark.parametrize("phase_code", [3, 4, 65535])
def test_unknown_phase_code_keeps_readings_but_disables_phase_switching(tmp_path, phase_code):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        service.identity.phase_options_hw = phase_code
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                assert remote.connected
                assert remote.identity.phase_options_hw == phase_code
                assert not phase_switch_is_supported(remote.identity.phase_options_hw)
                assert remote.snapshot["values"]
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["authentication", "not_ready", "snapshot", "json"])
def test_failure_categories_are_precise_and_do_not_expose_response_data(tmp_path, fault):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        try:
            if fault == "not_ready":
                service._success = False
            if fault == "snapshot":
                service.identity.phase_options_hw = -1
            app = create_app(service)
            if fault == "json":
                app = web.Application()

                async def invalid(_request):
                    return web.Response(text="secret-response-body")

                app.router.add_get("/v1/state", invalid)
            async with TestServer(app) as server, ClientSession() as session:
                token = "wrong" * 10 if fault == "authentication" else TOKEN
                remote = RemoteClient(session, str(server.make_url("/")), token)
                with pytest.raises(ServiceConnectionError) as raised:
                    await remote.async_connect()
                assert raised.value.reason == {
                    "authentication": "service_auth_failed", "not_ready": "service_not_ready",
                    "snapshot": "service_invalid_response", "json": "service_invalid_response",
                }[fault]
                if fault == "snapshot":
                    assert "identity.phase_options_hw" in str(raised.value)
                assert TOKEN not in str(raised.value)
                assert "secret-response-body" not in str(raised.value)
        finally:
            await service.stop()

    asyncio.run(run())
