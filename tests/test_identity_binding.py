"""Configured hardware identity must survive reconnects and service restarts."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import MappingProxyType

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, current_writes, service_fixture
from tests.test_service_reauth import add_entry, entity_ids

SERIAL = "ABC123456789"
OTHER_SERIAL = "DIFFERENT-WALLBOX"
NUMBER = "number.mennekes_amtron_charging_current_limit"


@pytest.mark.parametrize("transport_type", ["serial", "rtu_over_tcp", "modbus_tcp", "service"])
@pytest.mark.parametrize("binding", ["serial_number", "unique_id"])
def test_setup_rejects_another_wallbox_before_starting_controls(
    tmp_path, monkeypatch, caplog, transport_type, binding
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:
                if binding == "unique_id":
                    # Model an already stored serial binding whose data has
                    # always used the entry-ID entity fallback. Do not remove
                    # a serial field after entities were created from it.
                    assert await async_setup_component(hass, "network", {})
                    entry = ConfigEntry(
                        version=1, minor_version=1, domain=DOMAIN,
                        title="Mennekes Amtron", unique_id=SERIAL, source="user",
                        data={"server_url": str(server.make_url("/")), "server_token": TOKEN},
                        options={"scan_interval_seconds": 300},
                        discovery_keys=MappingProxyType({}), subentries_data=[],
                    )
                    await hass.config_entries.async_add(entry)
                    assert entry.state is ConfigEntryState.LOADED
                else:
                    entry = await add_entry(hass, str(server.make_url("/")))
                before = entity_ids(hass, entry)
                await hass.config_entries.async_unload(entry.entry_id)
                if transport_type == "service":
                    data = dict(entry.data)
                    service.identity.serial_number = OTHER_SERIAL
                else:
                    data = (
                        {"port": "/dev/fake", "connection_type": "serial"}
                        if transport_type == "serial" else
                        {"connection_type": transport_type, "host": "gateway.local", "tcp_port": 502}
                    )
                    bus = FakeModbusClient(device_bank(serial=OTHER_SERIAL))

                    def factory(config, **kwargs):
                        return MennekesModbusClient(config, client_factory=lambda _: bus, **kwargs)

                    monkeypatch.setattr(integration, "MennekesModbusClient", factory)
                if binding == "serial_number":
                    data["serial_number"] = SERIAL
                else:
                    data.pop("serial_number", None)
                hass.config_entries.async_update_entry(
                    entry, data=data, options={"control_mode": "master"}
                )
                assert not await hass.config_entries.async_setup(entry.entry_id)
                assert entry.state is ConfigEntryState.SETUP_RETRY
                assert entry.unique_id == SERIAL
                assert not bus.writes
                if transport_type != "service":
                    assert not bus.connected
                    assert bus.close_calls == 1
                assert OTHER_SERIAL not in caplog.text
                assert "gateway.local" not in caplog.text
                assert "/dev/fake" not in caplog.text
                # Restoring the intended device permits the ordinary retry,
                # without recreating the entry or issuing a charging command.
                service.identity.serial_number = SERIAL
                bus.bank = device_bank(serial=SERIAL)
                assert await hass.config_entries.async_reload(entry.entry_id)
                assert entry.state is ConfigEntryState.LOADED
                assert entry.runtime_data.identity.serial_number == SERIAL
                assert entry.unique_id == SERIAL
                assert entity_ids(hass, entry) == before
                assert not current_writes(bus)
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("replacement", [OTHER_SERIAL, None])
@pytest.mark.parametrize("command_order", ["after_poll", "queued", "before_poll"])
def test_runtime_service_identity_change_blocks_commands_and_recovers_in_place(
    tmp_path, replacement, command_order
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        pause_poll = False
        poll_started = asyncio.Event()
        release_poll = asyncio.Event()
        commands = []

        @web.middleware
        async def observe(request, handler):
            if request.path == "/v2/command":
                commands.append(request.path)
            if pause_poll and request.path == "/v2/state":
                poll_started.set()
                await release_poll.wait()
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, observe)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                runtime = entry.runtime_data
                before = entity_ids(hass, entry)
                accepted_snapshot = deepcopy(runtime.client.snapshot)
                service.identity.serial_number = replacement
                if command_order == "queued":
                    pause_poll = True
                    polling = asyncio.create_task(runtime.coordinator.async_refresh())
                    await asyncio.wait_for(poll_started.wait(), 2)
                    command = asyncio.create_task(runtime.control.async_set_charging_current(10))
                    await asyncio.sleep(0)
                    assert not command.done()
                    release_poll.set()
                    await polling
                    with pytest.raises(HomeAssistantError):
                        await command
                    pause_poll = False
                elif command_order == "after_poll":
                    await runtime.coordinator.async_refresh()
                    with pytest.raises(HomeAssistantError):
                        await runtime.control.async_set_charging_current(10)
                else:
                    # The server changed after the last valid HA poll. The
                    # command's identity precondition must reject it before
                    # Modbus I/O and notify HA without waiting for a poll.
                    with pytest.raises(HomeAssistantError) as error:
                        await runtime.control.async_set_charging_current(10)
                    assert error.value.translation_key == "device_identity_changed"
                await hass.async_block_till_done()
                assert not runtime.coordinator.last_update_success
                assert runtime.coordinator.last_exception.translation_key == "device_identity_changed"
                assert runtime.client.identity.serial_number == SERIAL
                assert runtime.client.snapshot == accepted_snapshot
                assert not runtime.client.connected
                assert hass.states.get(NUMBER).state == "unavailable"
                attempted = 1 if command_order == "before_poll" else 0
                assert len(commands) == attempted
                assert not current_writes(bus)
                with pytest.raises(ValueError, match="already bound"):
                    runtime.client.bind_identity(OTHER_SERIAL)
                service.identity.serial_number = SERIAL
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert runtime.coordinator.last_update_success
                assert runtime.client.connected
                assert hass.states.get(NUMBER).state != "unavailable"
                assert entity_ids(hass, entry) == before
                assert entry.unique_id == SERIAL
                assert len(commands) == attempted
                assert not current_writes(bus)
                await runtime.control.async_set_charging_current(10)
                assert current_writes(bus) == [10]
        finally:
            release_poll.set()
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("available_serial", [None, SERIAL])
def test_legacy_endpoint_identity_remains_usable_and_is_not_reassigned(
    tmp_path, available_serial
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path, read_only=True)
        service.identity.serial_number = None
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:
                url = str(server.make_url("/"))
                entry = await add_entry(hass, url, expected_serial=None)
                old_unique_id = f"{DOMAIN}_{url.rstrip('/')}"
                before = entity_ids(hass, entry)
                await hass.config_entries.async_unload(entry.entry_id)
                service.identity.serial_number = available_serial
                assert await hass.config_entries.async_setup(entry.entry_id)
                assert entry.unique_id == old_unique_id
                assert "serial_number" not in entry.data
                assert entity_ids(hass, entry) == before
                assert entry.state is ConfigEntryState.LOADED
                if available_serial is not None:
                    service.identity.serial_number = OTHER_SERIAL
                    await entry.runtime_data.coordinator.async_refresh()
                    assert not entry.runtime_data.coordinator.last_update_success
                    assert entry.runtime_data.client.identity.serial_number == SERIAL
                assert not bus.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
