"""Command rejection must invalidate stale HA permission before its next poll."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError

from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import current_writes, service_fixture
from tests.test_service_reauth import add_entry, entity_ids

NUMBER = "number.mennekes_amtron_charging_current_limit"
POWER = "sensor.mennekes_amtron_total_power"


@pytest.mark.parametrize("reason", ["stale", "read_only"])
def test_rejected_command_immediately_invalidates_cached_availability(tmp_path, reason):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        requests = []

        @web.middleware
        async def observe(request, handler):
            requests.append((request.method, request.path))
            return await handler(request)

        app = create_app(service)
        app.middlewares.append(observe)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                runtime = entry.runtime_data
                before = entity_ids(hass, entry)
                assert hass.states.get(NUMBER).state != "unavailable"
                if reason == "stale":
                    service._updated_at -= 3600
                    error_key = "service_unavailable"
                else:
                    service.config = replace(service.config, read_only=True)
                    service.control._options = replace(service.control.options, read_only=True)
                    error_key = "read_only_mode"
                requests.clear()
                with pytest.raises(HomeAssistantError) as error:
                    await runtime.control.async_set_charging_current(10)
                assert error.value.translation_key == error_key
                await hass.async_block_till_done()
                assert not runtime.client.connected
                assert not runtime.coordinator.last_update_success
                assert hass.states.get(NUMBER).state == "unavailable"
                assert requests == [("POST", "/v2/command")]
                assert not current_writes(bus)

                # Only the normal next poll may restore availability. A
                # changed service policy still blocks controls after that.
                await service.refresh()
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert runtime.client.connected
                assert runtime.coordinator.last_update_success
                assert hass.states.get(POWER).state != "unavailable"
                assert (hass.states.get(NUMBER).state == "unavailable") is (reason == "read_only")
                # The app's charging job is read along with its state.
                assert requests == [("POST", "/v2/command"), ("GET", "/v2/state"), ("GET", "/v2/jobs")]
                assert not current_writes(bus)
                if reason == "read_only":
                    service.config = replace(service.config, read_only=False)
                    service.control._options = replace(service.control.options, read_only=False)
                    await runtime.coordinator.async_refresh()
                    await hass.async_block_till_done()
                    assert hass.states.get(NUMBER).state != "unavailable"
                assert entity_ids(hass, entry) == before
                assert not current_writes(bus)
                assert requests.count(("POST", "/v2/command")) == 1
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
