"""An in-flight reauth must not overwrite a newly configured service."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import current_writes, service_fixture
from tests.test_service_reauth import NEW_TOKEN, add_entry, reauth

OTHER_TOKEN = "different-service-token-32-characters"  # noqa: S105 - test fixture


@pytest.mark.parametrize("change", ["endpoint", "credentials"])
@pytest.mark.parametrize("probe", ["success", "auth", "unavailable"])
@pytest.mark.parametrize("flow_kind", ["reauth", "reconfigure"])
def test_inflight_probe_cannot_overwrite_changed_connection(tmp_path, change, probe, flow_kind):
    async def run():
        (tmp_path / "old").mkdir()
        (tmp_path / "new").mkdir()
        old, old_transport, _clock = await service_fixture(tmp_path / "old", read_only=True)
        new, new_transport, _clock = await service_fixture(tmp_path / "new", read_only=True)
        new.config = replace(new.config, token=OTHER_TOKEN)
        hass = await _boot(tmp_path / "ha")
        started = asyncio.Event()
        release = asyncio.Event()
        intercept = False
        received = []

        @web.middleware
        async def delay_authenticated_probe(request, handler):
            if intercept and request.path == "/v2/state" and request.headers.get("Authorization") == f"Bearer {NEW_TOKEN}":
                started.set()
                await release.wait()
                if probe != "success":
                    return web.json_response({}, status=401 if probe == "auth" else 503)
                # The old endpoint accepted this credential at probe time.
                return web.json_response(old.snapshot())
            return await handler(request)

        @web.middleware
        async def observe_new_service(request, handler):
            received.append(request.headers.get("Authorization"))
            return await handler(request)

        old_app = create_app(old)
        old_app.middlewares.insert(0, delay_authenticated_probe)
        new_app = create_app(new)
        new_app.middlewares.insert(0, observe_new_service)
        task = None
        try:
            async with TestServer(old_app) as old_server, TestServer(new_app) as new_server:
                entry = await add_entry(hass, str(old_server.make_url("/")))
                if flow_kind == "reauth":
                    flow = await reauth(hass, entry)
                    assert flow["step_id"] == "reauth_confirm"
                    submitted = {"server_token": NEW_TOKEN}
                else:
                    flow = await hass.config_entries.flow.async_init(
                        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id},
                    )
                    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "server"})
                    submitted = {"server_url": str(old_server.make_url("/")), "server_token": NEW_TOKEN}
                intercept = True
                task = asyncio.create_task(hass.config_entries.flow.async_configure(
                    flow["flow_id"], submitted,
                ))
                await asyncio.wait_for(started.wait(), 5)

                # A Supervisor discovery or another actor updates the same entry
                # while the previously selected endpoint is being validated.
                replacement = {**entry.data, "server_token": OTHER_TOKEN}
                if change == "endpoint":
                    replacement["server_url"] = str(new_server.make_url("/")).rstrip("/")
                else:
                    old.config = replace(old.config, token=OTHER_TOKEN)
                hass.config_entries.async_update_entry(entry, data=replacement)
                await hass.async_block_till_done()
                assert dict(entry.data) == replacement
                assert entry.state is ConfigEntryState.LOADED

                release.set()
                result = await task
                await hass.async_block_till_done()
                assert result["type"] == "abort"
                assert result["reason"] == "connection_settings_changed"
                assert dict(entry.data) == replacement
                assert entry.state is ConfigEntryState.LOADED
                assert f"Bearer {NEW_TOKEN}" not in received
                assert not current_writes(old_transport)
                assert not current_writes(new_transport)
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await hass.async_stop()
            await old.stop()
            await new.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
