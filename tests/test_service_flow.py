"""Service setup and migration retain device identity without sending commands."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.ha_fakes import FakeHass
from tests.test_config_flow import Flow
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


@pytest.mark.parametrize("version", [0, 2])
def test_ha_rejects_incompatible_entry_schema_without_placeholder_migration(
    tmp_path, monkeypatch, version
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(create_app(service)) as server:
                flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"next_step_id": "server"}
                )
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {"server_url": str(server.make_url("/")), "server_token": TOKEN},
                )
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                assert entry.state is ConfigEntryState.LOADED
                await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(entry, version=version)
                setup = AsyncMock(wraps=integration.async_setup_entry)
                monkeypatch.setattr(integration, "async_setup_entry", setup)
                assert not await hass.config_entries.async_setup(entry.entry_id)
                assert entry.state is ConfigEntryState.MIGRATION_ERROR
                setup.assert_not_awaited()
                assert not transport.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    "scenario",
    [
        "new",
        "read_only",
        "no_serial",
        "reconfigure",
        "listeners",
        "migration_no_serial",
        "bad_token",
        "bad_url",
        "server_down",
    ],
)
def test_service_form_validates_identity_credentials_and_backend(
    tmp_path, monkeypatch, scenario
):
    async def run():
        service, transport, _clock = await service_fixture(
            tmp_path, read_only=scenario == "read_only"
        )
        if scenario in ("no_serial", "migration_no_serial"):
            service.identity.serial_number = None
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                monkeypatch.setattr(
                    config_flow, "async_get_clientsession", lambda _hass: session
                )
                flow = Flow(FakeHass())
                reconfigure = scenario in (
                    "reconfigure",
                    "listeners",
                    "migration_no_serial",
                )
                flow.context = {"source": "reconfigure" if reconfigure else "user"}
                flow.reconfigure_entry.data = {"port": "/dev/old"}
                flow.reconfigure_entry.update_listeners = (
                    [lambda: None] if scenario == "listeners" else []
                )
                result = await flow.async_step_server()
                assert result["step_id"] == "server"
                values = {
                    "server_url": str(server.make_url("/")),
                    "server_token": TOKEN,
                }
                if scenario == "bad_token":
                    values["server_token"] = "short"  # noqa: S105 - deliberately invalid test value
                if scenario == "bad_url":
                    values["server_url"] = "ftp://localhost"
                if scenario == "server_down":
                    await server.close()
                result = await flow.async_step_server(values)
                if scenario in ("bad_token", "bad_url", "server_down"):
                    assert result["errors"] == {"base": "cannot_connect_service"}
                elif scenario == "migration_no_serial":
                    assert result["reason"] == "service_migration_requires_serial"
                elif reconfigure:
                    assert result["type"] == "abort"
                    assert "port" not in flow.updates
                    assert flow.updates["server_url"] == values["server_url"].rstrip(
                        "/"
                    )
                else:
                    assert result["type"] == "create_entry"
                    assert "control_mode" not in result.get("options", {})
                assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


def test_reconfiguring_a_real_entry_preserves_entity_ids(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        assert await async_setup_component(hass, "network", {})
        try:
            with patch(
                "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
                return_value=transport,
            ):
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"next_step_id": "serial"}
                )
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "port": "/dev/fake",
                        "device_id": 50,
                        "baudrate": 57600,
                        "frame": "8n2",
                        "search": "off",
                    },
                )
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
            entry = hass.config_entries.async_entries(DOMAIN)[0]
            entry_id = entry.entry_id
            assert entry_id in hass.data[f"{DOMAIN}_write_states"]
            from homeassistant.helpers import entity_registry as er

            registry = er.async_get(hass)
            entities = {
                entity.unique_id: (entity.entity_id, entity.device_id)
                for entity in er.async_entries_for_config_entry(registry, entry_id)
            }
            assert await hass.config_entries.async_unload(entry_id)
            # Our fake adapter is shared only to model the device bank; reopen
            # it after the direct entry released ownership.
            await transport.connect()
            async with TestServer(create_app(service)) as server:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "reconfigure", "entry_id": entry_id}
                )
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"next_step_id": "server"}
                )
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {"server_url": str(server.make_url("/")), "server_token": TOKEN},
                )
                assert result["type"] == "abort"
                await hass.async_block_till_done()
                assert entry.entry_id == entry_id
                assert isinstance(entry.runtime_data.client, RemoteClient)
                assert entry_id not in hass.data[f"{DOMAIN}_write_states"]
                assert "port" not in entry.data
                migrated = {
                    entity.unique_id: (entity.entity_id, entity.device_id)
                    for entity in er.async_entries_for_config_entry(registry, entry_id)
                }
                assert entities.items() <= migrated.items()
                # The direct entry was read-only. The service now owns write
                # access, so migration exposes all controls, and the app's
                # status sensors.
                assert {
                    value[0].split(".", 1)[0]
                    for key, value in migrated.items() if key not in entities
                } == {"number", "switch", "select", "button", "sensor"}
                assert not entry.runtime_data.control.read_only
                assert not current_writes(transport)
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
