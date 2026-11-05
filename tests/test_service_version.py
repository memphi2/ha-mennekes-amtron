"""An older service requests a coordinated update, not new credentials."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_service_reauth import add_entry


@pytest.mark.parametrize("phase", ["flow", "setup", "poll"])
@pytest.mark.parametrize("version", [1, 3, "missing_route", True, "2"])
def test_api_mismatch_is_distinct_from_auth_failure_and_recovers(
    tmp_path, phase, version
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        incompatible = phase == "flow"
        paths = []

        @web.middleware
        async def older_service(request, handler):
            paths.append(request.path)
            if incompatible and request.path == "/v2/state":
                if version == "missing_route":
                    return web.Response(status=404)
                payload = service.snapshot()
                payload["api_version"] = version
                return web.json_response(payload)
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, older_service)
        expected = (
            "service_version_mismatch"
            if type(version) is int or version == "missing_route" else "service_invalid_response"
        )
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(app) as server:
                url = str(server.make_url("/"))
                if phase == "flow":
                    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                    flow = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"next_step_id": "server"}
                    )
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
                    )
                    assert result["errors"] == {"base": expected}
                    assert not hass.config_entries.async_entries(DOMAIN)
                    incompatible = False
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
                    )
                    assert result["type"] == "create_entry"
                    await hass.async_block_till_done()
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                else:
                    entry = await add_entry(hass, url)
                    incompatible = True
                    if phase == "setup":
                        assert not await hass.config_entries.async_reload(entry.entry_id)
                        assert entry.state is ConfigEntryState.SETUP_RETRY
                    else:
                        await entry.runtime_data.coordinator.async_refresh()
                        assert not entry.runtime_data.coordinator.last_update_success
                        if expected == "service_version_mismatch":
                            assert entry.runtime_data.coordinator.last_exception.translation_key == expected
                    await hass.async_block_till_done()
                    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                    incompatible = False
                    if phase == "setup":
                        assert await hass.config_entries.async_reload(entry.entry_id)
                    else:
                        await entry.runtime_data.coordinator.async_refresh()
                assert entry.unique_id == "ABC123456789"
                assert entry.state is ConfigEntryState.LOADED
                assert entry.runtime_data.coordinator.last_update_success
                assert all(path.startswith("/v2/") for path in paths)
                assert not bus.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
