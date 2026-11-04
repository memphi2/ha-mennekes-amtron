"""Exercise actual HA shutdown and startup against a still-running service."""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


@pytest.mark.parametrize("paused", [False, True])
def test_real_ha_restart_retains_server_state_without_replaying_commands(
    tmp_path, paused
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        config = tmp_path / "config"
        try:
            async with TestServer(create_app(service)) as server:
                # Any attempt by HA to open the serial port is a regression.
                with patch(
                    "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
                    side_effect=AssertionError("HA opened the serial port"),
                ):
                    hass = await _boot(config)
                    assert await async_setup_component(hass, "network", {})
                    try:
                        flow = await hass.config_entries.flow.async_init(
                            DOMAIN, context={"source": "user"}
                        )
                        assert flow["type"] == "menu"
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
                        assert result["type"] == "create_entry"
                        await hass.async_block_till_done()
                        entry = hass.config_entries.async_entries(DOMAIN)[0]
                        assert entry.runtime_data.recovery is None
                        dump = await async_get_config_entry_diagnostics(hass, entry)
                        assert dump["bus"] == {
                            "transport": "service",
                            "connected": True,
                        }
                        assert TOKEN not in repr(dump)
                        assert str(server.make_url("/")) not in repr(dump)
                        assert service.identity.serial_number not in repr(dump)
                        if paused:
                            await hass.services.async_call(
                                "switch",
                                "turn_on",
                                {"entity_id": "switch.mennekes_amtron_charging_paused"},
                                blocking=True,
                            )
                        else:
                            await hass.services.async_call(
                                "number",
                                "set_value",
                                {
                                    "entity_id": "number.mennekes_amtron_charging_current_limit",
                                    "value": 10,
                                },
                                blocking=True,
                            )
                        await hass.async_block_till_done()
                        accepted = current_writes(transport)
                        assert accepted == ([1] if paused else [10])
                    finally:
                        await hass.async_stop()
                    before = service.diagnostics.heartbeats_sent
                    await asyncio.sleep(0.08)
                    assert service.diagnostics.heartbeats_sent > before
                    assert service.heartbeat.running
                    assert current_writes(transport) == accepted
                    shutil.rmtree(config / "custom_components" / DOMAIN)
                    hass = await _boot(config)
                    assert await async_setup_component(hass, "network", {})
                    try:
                        entry = hass.config_entries.async_entries(DOMAIN)[0]
                        assert await hass.config_entries.async_setup(entry.entry_id)
                        await hass.async_block_till_done()
                        assert hass.states.get(
                            "switch.mennekes_amtron_charging_paused"
                        ).state == ("on" if paused else "off")
                        assert float(
                            hass.states.get(
                                "number.mennekes_amtron_charging_current_limit"
                            ).state
                        ) == (8 if paused else 10)
                        assert current_writes(transport) == accepted
                        assert service.heartbeat.running
                    finally:
                        await hass.async_stop()
        finally:
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
