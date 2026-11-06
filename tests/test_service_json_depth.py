"""A response byte cap must also contain failures in JSON decoding."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.test_modbus_service import TOKEN, service_fixture


def test_deep_service_reply_invalidates_cached_connection(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        # Within the client's 128 KiB cap but deep enough to exceed the Python
        # 3.14 JSON decoder's C-stack budget on supported platforms.
        nested = '{"nested":' + "[" * 60000 + "0" + "]" * 60000 + "}"

        async def reply(_request):
            return web.Response(text=nested, content_type="application/json")

        app = web.Application()
        app.router.add_get("/v2/state", reply)
        try:
            async with TestServer(app) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                original = service.snapshot()
                remote.apply_snapshot(original)
                notifications = []
                remote.on_update = lambda: notifications.append(remote.connected)
                with pytest.raises(ServiceConnectionError) as error:
                    await remote.update()
                assert error.value.reason == "service_invalid_response"
                assert not remote.connected
                assert remote.snapshot is original
                assert notifications == [False]
        finally:
            await service.stop()

    asyncio.run(run())
