"""A running HA follows service write permission without recreating entities."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, current_writes, service_fixture

CONTROL_ENTITY_IDS = (
    "number.mennekes_amtron_charging_current_limit",
    "switch.mennekes_amtron_charging_paused",
    "switch.mennekes_amtron_charging_release",
    "switch.mennekes_amtron_wallbox_locked",
    "select.mennekes_amtron_solar_charging_mode",
    "select.mennekes_amtron_requested_phases",
    "button.mennekes_amtron_recover_from_error",
    "button.mennekes_amtron_restart",
)


@pytest.mark.parametrize("initial_read_only", [False, True])
def test_service_permission_controls_real_ha_entities_and_commands(
    tmp_path, initial_read_only
):
    """Old HA read-only options cannot override the app's live permission."""

    async def run():
        service, transport, _clock = await service_fixture(
            tmp_path, read_only=initial_read_only
        )
        hass = await _boot(tmp_path / "ha")
        fault = None
        commands = []

        @web.middleware
        async def observe(request, handler):
            if request.path == "/v1/command":
                commands.append(request.path)
            if request.path == "/v1/state" and fault:
                if fault == "offline":
                    return web.json_response(
                        {"error": "service_unavailable"}, status=503
                    )
                snapshot = deepcopy(service.snapshot())
                if fault == "not_ready":
                    snapshot["available"] = False
                else:
                    snapshot["control"]["read_only"] = "invalid"
                return web.json_response(snapshot)
            return await handler(request)

        app = create_app(service)
        app.middlewares.append(observe)
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(app) as server:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"next_step_id": "server"}
                )
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "server_url": str(server.make_url("/")),
                        "server_token": TOKEN,
                    },
                )
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                # Model an installed entry from before the service became the
                # permission source. Its useful current cap must survive.
                hass.config_entries.async_update_entry(
                    entry,
                    options={
                        "control_mode": "read_only",
                        "current_limit": 10,
                        "scan_interval_seconds": 300,
                    },
                )
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                registry = er.async_get(hass)

                def entity_ids():
                    return {
                        entity.unique_id: (entity.entity_id, entity.device_id)
                        for entity in er.async_entries_for_config_entry(
                            registry, entry.entry_id
                        )
                    }

                def assert_controls(read_only):
                    for entity_id in CONTROL_ENTITY_IDS:
                        state = hass.states.get(entity_id)
                        assert state is not None, entity_id
                        assert (state.state == "unavailable") is read_only, (
                            entity_id,
                            state,
                        )
                    assert runtime.control.read_only is read_only

                async def refresh():
                    await runtime.coordinator.async_refresh()
                    await hass.async_block_till_done()
                    assert entry.runtime_data is runtime
                    assert entity_ids() == initial_ids
                    assert runtime.coordinator.data.values == measurements

                async def assert_command_blocked():
                    accepted = len(commands)
                    with pytest.raises(HomeAssistantError):
                        await runtime.control.async_pause_charging()
                    assert len(commands) == accepted
                    assert not current_writes(transport)

                initial_ids = entity_ids()
                measurements = deepcopy(runtime.coordinator.data.values)
                assert_controls(initial_read_only)
                power = hass.states.get("sensor.mennekes_amtron_total_power")
                assert power is not None
                assert power.state != "unavailable"
                assert entry.options["control_mode"] == "read_only"

                for read_only in (True, False, True, False):
                    # An app restart publishes its replacement configuration
                    # at the same URL. Keep the readings identical so an
                    # always_update=False coordinator must notice permission.
                    service.config = replace(service.config, read_only=read_only)
                    service.control._options = replace(
                        service.control.options, read_only=read_only
                    )
                    await refresh()
                    assert_controls(read_only)
                    assert hass.states.get(power.entity_id).last_updated == power.last_updated
                    if read_only:
                        await assert_command_blocked()

                # Neither reconnecting nor changing permission may issue a
                # command, replay a setpoint, or change the retained pause.
                assert commands == []
                assert not [
                    address
                    for address, _words in transport.writes
                    if address != R.HEARTBEAT.address
                ]
                number = hass.states.get(CONTROL_ENTITY_IDS[0])
                assert number.attributes["max"] == 10

                for fault in ("offline", "not_ready", "malformed"):
                    await refresh()
                    assert_controls(True)
                    await assert_command_blocked()
                    fault = None
                    await refresh()
                    assert_controls(False)

                # The old read-only selector is ignored, but the independent
                # HA current cap still constrains an actual service command.
                await runtime.control.async_set_charging_current(32)
                assert current_writes(transport) == [10]
                assert len(commands) == 1
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
