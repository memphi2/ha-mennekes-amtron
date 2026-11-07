"""Configure the physical gate before heartbeat and clean up all observers."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_client, _patch_issues


def test_direct_gate_is_enforced_before_heartbeat_and_stops_observer(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "virtual_plug_charge": True})
        observer = Mock(async_stop=AsyncMock())
        monkeypatch.setattr(integration, "PlugChargeObserver", lambda *_: observer)
        start = integration._start_heartbeat

        def heartbeat(*args):
            assert transport.bank[R.CHARGING_RELEASE.address] == 0
            return start(*args)

        monkeypatch.setattr(integration, "_start_heartbeat", heartbeat)
        assert await integration.async_setup_entry(hass, entry)
        observer.start.assert_called_once()
        assert entry.runtime_data.control.plug_charge["enabled"]
        assert await integration.async_unload_entry(hass, entry)
        observer.async_stop.assert_awaited_once()
        assert entry.runtime_data.coordinator.observe_plug_charge is None

    asyncio.run(run())


def test_read_only_configuration_cannot_silently_enable_a_gate(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={"virtual_plug_charge": True})
        with pytest.raises(ConfigEntryNotReady) as error:
            await integration.async_setup_entry(hass, entry)
        assert error.value.translation_key == "plug_charge_unavailable"
        assert not entry.runtime_data.client.connected
        assert not transport.writes

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["loaded", "unload_rejected", "setup_skipped", "connection_changed"])
def test_profile_reload_hands_over_only_the_same_open_connection(tmp_path, monkeypatch, outcome):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "virtual_plug_charge": True})
        monkeypatch.setattr(integration, "PlugChargeObserver", lambda *_: Mock(async_stop=AsyncMock()))
        assert await integration.async_setup_entry(hass, entry)
        old = entry.runtime_data
        transport.bank[R.EVSE_STATE.address] = 2
        transport.bank[R.CP_STATE.address] = 11
        await old.coordinator.async_refresh()
        token, since = old.control.plug_charge["session"], old.control.plug_charge["since"]
        assert isinstance(token, str)

        async def reload(_entry_id):
            if outcome == "unload_rejected":
                hass.config_entries.unload_result = False
                assert not await integration.async_unload_entry(hass, entry)
                return
            assert await integration.async_unload_entry(hass, entry)
            assert old.client.connected
            if outcome == "setup_skipped":
                return
            if outcome == "connection_changed":
                entry.data = {**entry.data, "port": "/dev/other"}
            assert await integration.async_setup_entry(hass, entry)
            if outcome == "loaded":
                assert entry.runtime_data.client is old.client
                assert entry.runtime_data.control is not old.control
                assert entry.runtime_data.control.plug_charge["session"] == token
                assert entry.runtime_data.control.plug_charge["since"] == since
            else:
                assert entry.runtime_data.client is not old.client
                assert entry.runtime_data.control.plug_charge["session"] != token

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        try:
            await integration.async_reload_entry(hass, entry)
            assert not old.retain_client_on_unload
            assert not hass.data.get("mennekes_amtron_rtu_reload_clients", {})
            if outcome == "setup_skipped":
                assert not old.client.connected
            elif outcome == "unload_rejected":
                assert old.client.connected
                assert old.control.plug_charge["session"] == token
        finally:
            hass.config_entries.unload_result = True
            if outcome != "setup_skipped":
                assert await integration.async_unload_entry(hass, entry)
            assert not entry.runtime_data.client.connected

    asyncio.run(run())


def test_overlapping_profile_reloads_preserve_the_successor_client_and_session(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "virtual_plug_charge": True})
        monkeypatch.setattr(integration, "PlugChargeObserver", lambda *_: Mock(async_stop=AsyncMock()))
        assert await integration.async_setup_entry(hass, entry)
        client = entry.runtime_data.client
        transport.bank[R.EVSE_STATE.address] = 2
        transport.bank[R.CP_STATE.address] = 11
        await entry.runtime_data.coordinator.async_refresh()
        token = entry.runtime_data.control.plug_charge["session"]
        started, finish = asyncio.Event(), asyncio.Event()
        owners = []

        async def reload(_entry_id):
            owners.append(entry.runtime_data)
            if len(owners) == 1:
                started.set()
                await finish.wait()
            assert await integration.async_unload_entry(hass, entry)
            assert client.connected
            assert await integration.async_setup_entry(hass, entry)
            assert entry.runtime_data.client is client
            assert entry.runtime_data.control.plug_charge["session"] == token

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        first = asyncio.create_task(integration.async_reload_entry(hass, entry))
        await started.wait()
        second = asyncio.create_task(integration.async_reload_entry(hass, entry))
        await asyncio.sleep(0)
        assert len(owners) == 1
        finish.set()
        try:
            await asyncio.gather(first, second)
            assert len(owners) == 2
            assert owners[0] is not owners[1]
            assert all(not owner.retain_client_on_unload for owner in owners)
            assert entry.runtime_data.control.plug_charge["session"] == token
        finally:
            finish.set()
            await integration.async_unload_entry(hass, entry)
        assert not client.connected

    asyncio.run(run())
