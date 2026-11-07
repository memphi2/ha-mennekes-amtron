"""Remote authentication failures replace stale connection repairs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mennekes_amtron_rtu import repair_health
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteCoordinator
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.ha_fakes import build_runtime
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import service_fixture
from tests.test_service_reauth import NEW_TOKEN, add_entry


@pytest.mark.parametrize("trigger", ["poll", "diagnostics"])
def test_remote_outage_followed_by_auth_failure_clears_repair(tmp_path, monkeypatch, trigger):
    """An already-unavailable coordinator must still publish a new auth error."""

    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        outage = False

        @web.middleware
        async def fail_while_offline(request, handler):
            if outage:
                return web.json_response({}, status=503)
            return await handler(request)

        monkeypatch.setattr(repair_health, "HEARTBEAT_REPAIR_DELAY", 0.02)
        app = create_app(service)
        app.middlewares.insert(0, fail_while_offline)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                runtime = entry.runtime_data
                registry = ir.async_get(hass)
                issue_id = f"{entry.entry_id}_service_unavailable"
                outage = True
                await runtime.coordinator.async_refresh()
                await asyncio.sleep(0.04)
                assert registry.async_get_issue(DOMAIN, issue_id) is not None

                outage = False
                service.config = replace(service.config, token=NEW_TOKEN)

                async def reject():
                    if trigger == "poll":
                        await runtime.coordinator.async_refresh()
                    else:
                        with pytest.raises(ServiceConnectionError):
                            await runtime.client.request(
                                "GET", "/v2/diagnostics", update_connection=False,
                            )

                with patch.object(
                    runtime.coordinator, "async_update_listeners",
                    wraps=runtime.coordinator.async_update_listeners,
                ) as notify:
                    await reject()
                    assert isinstance(runtime.coordinator.last_exception, ConfigEntryAuthFailed)
                    assert registry.async_get_issue(DOMAIN, issue_id) is None
                    assert notify.call_count == 1
                    await reject()
                    assert notify.call_count == 1
                await hass.async_block_till_done()
                flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                assert len(flows) == 1
                assert flows[0]["step_id"] == "reauth_confirm"
                assert registry.async_get_issue(DOMAIN, issue_id) is None
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_remote_coordinator_rejects_a_direct_client():
    """A miswired coordinator must not poll an unrelated local transport."""

    async def run():
        hass, entry, _transport = await build_runtime()
        runtime = entry.runtime_data
        try:
            coordinator = RemoteCoordinator(
                hass, entry, client=runtime.client, identity=runtime.identity,
                connection_state=runtime.connection_state, scan_interval=5,
            )
            with pytest.raises(UpdateFailed, match="service client missing"):
                await coordinator._async_update_data()
        finally:
            await runtime.client.async_close()

    asyncio.run(run())
