"""Load real HA against real LAN traffic, directly and through the service API."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from tests.fakes import device_bank
from tests.options_flow_helpers import async_options
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN
from tests.test_network_wire import PROTOCOLS, network_peer, register_reply


async def add_connection(hass, step, data):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": step}
    )
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], data)
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == "ABC123456789"
    return entry


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_real_ha_network_setup_read_only_master_pause_and_unload(tmp_path, protocol):
    async def run():
        bank = device_bank(charging_current=1)
        async with network_peer(
            protocol, lambda payload, _number: register_reply(payload, bank)
        ) as (port, requests, writers):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await add_connection(hass, "network", {
                    "host": "127.0.0.1", "tcp_port": port,
                    "connection_type": protocol, "device_id": 50,
                })
                assert entry.runtime_data.client.config.connection_type == protocol
                assert entry.runtime_data.control.read_only
                assert all(payload[1] == 3 for payload in requests)
                await async_options(
                    hass, entry, "wallbox", "wallbox_basic",
                    {"control_mode": "master", "current_limit": 16, "scan_interval_seconds": 5},
                    "edit", "wallbox_advanced",
                    {"measurement_interval_seconds": 30, "state_change_interval_seconds": 300, "phase_switch_grace_seconds": 5},
                    "save",
                )
                await hass.async_block_till_done()
                assert not entry.runtime_data.control.read_only
                heartbeat = entry.runtime_data.heartbeat
                assert heartbeat.running
                assert await heartbeat.async_beat_once()
                assert hass.states.get("switch.mennekes_amtron_charging_paused").state == "on"
                # Choosing a network connection or enabling control must never
                # send a current/resume command to a deliberately paused box.
                assert not any(payload[1] == 16 for payload in requests)
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert not heartbeat.running
                async with asyncio.timeout(1):
                    await asyncio.gather(*(writer.wait_closed() for writer in writers))
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 45))


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_real_ha_restart_keeps_service_lan_heartbeat_pause_and_entity_ids(tmp_path, protocol):
    async def run():
        bank = device_bank(charging_current=1)
        async with network_peer(
            protocol, lambda payload, _number: register_reply(payload, bank)
        ) as (port, requests, _writers):
            service = ModbusService(ServerConfig.from_dict({
                "connection_type": protocol, "host": "127.0.0.1",
                "tcp_port": port, "token": TOKEN, "read_only": False,
            }), tmp_path / "intent.json")
            service.heartbeat._interval = 0.05
            service.heartbeat._deadline = 1
            try:
                await service._initialize()
                async with TestServer(create_app(service)) as server:
                    directory = tmp_path / "ha"
                    hass = await _boot(directory)
                    try:
                        assert await async_setup_component(hass, "network", {})
                        entry = await add_connection(hass, "server", {
                            "server_url": str(server.make_url("/")), "server_token": TOKEN,
                        })
                        assert hass.states.get("switch.mennekes_amtron_charging_paused").state == "on"
                        original_ids = {
                            entity.entity_id: entity.unique_id
                            for entity in er.async_entries_for_config_entry(
                                er.async_get(hass), entry.entry_id
                            )
                        }
                    finally:
                        await hass.async_stop()
                    before = service.diagnostics.heartbeats_sent
                    await asyncio.sleep(0.15)
                    assert service.diagnostics.heartbeats_sent > before
                    assert service.heartbeat.healthy
                    shutil.rmtree(directory / "custom_components" / DOMAIN)
                    hass = await _boot(directory)
                    try:
                        assert await async_setup_component(hass, "network", {})
                        entry = hass.config_entries.async_entries(DOMAIN)[0]
                        assert await hass.config_entries.async_setup(entry.entry_id)
                        await hass.async_block_till_done()
                        assert hass.states.get("switch.mennekes_amtron_charging_paused").state == "on"
                        assert original_ids == {
                            entity.entity_id: entity.unique_id
                            for entity in er.async_entries_for_config_entry(
                                er.async_get(hass), entry.entry_id
                            )
                        }
                        assert not any(payload[1] == 16 for payload in requests)
                        assert service.heartbeat.healthy
                    finally:
                        await hass.async_stop()
            finally:
                await service.stop()

    asyncio.run(asyncio.wait_for(run(), 45))
