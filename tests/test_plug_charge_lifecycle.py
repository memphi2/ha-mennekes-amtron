"""Configure the physical gate before heartbeat and clean up all observers."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_client, _patch_issues


def test_direct_gate_is_enforced_at_setup_and_stops_observer(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "virtual_plug_charge": True})
        observer = Mock(async_stop=AsyncMock())
        monkeypatch.setattr(integration, "PlugChargeObserver", lambda *_: observer)
        assert await integration.async_setup_entry(hass, entry)
        observer.start.assert_called_once()
        assert entry.runtime_data.control.plug_charge["enabled"]
        # The gate holds the release until a car is granted.
        assert transport.bank[R.CHARGING_RELEASE.address] == 0
        assert _service(hass, entry).control.plug_charge["enabled"]
        assert await integration.async_unload_entry(hass, entry)
        observer.async_stop.assert_awaited_once()
        assert entry.runtime_data.coordinator.observe_estimates is None

    asyncio.run(run())


def test_read_only_configuration_loads_without_enabling_the_gate(tmp_path, monkeypatch, caplog):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={"virtual_plug_charge": True})
        # Retrying the setup would never gain write access.
        assert await integration.async_setup_entry(hass, entry)
        assert entry.runtime_data.control.plug_charge.get("enabled") is not True
        assert "not applied while the wallbox is read-only" in caplog.text
        assert not transport.writes
        assert await integration.async_unload_entry(hass, entry)

    asyncio.run(run())


def _host(hass, entry):
    return hass.data.get(f"{DOMAIN}_embedded", {}).get(entry.entry_id)


def _service(hass, entry):
    return _host(hass, entry)._service


async def _setup(hass, entry):
    """Set the entry up with the states Home Assistant gives it around setup."""
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    assert await integration.async_setup_entry(hass, entry)
    entry.state = ConfigEntryState.LOADED


async def _unload(hass, entry):
    assert await integration.async_unload_entry(hass, entry)
    entry.state = ConfigEntryState.NOT_LOADED


async def _plug_in(hass, entry, transport):
    """A car plugs in; the service gives the connection its session token."""
    transport.bank[R.EVSE_STATE.address] = 2
    transport.bank[R.CP_STATE.address] = 11
    await _service(hass, entry).refresh()
    await entry.runtime_data.coordinator.async_refresh()
    gate = entry.runtime_data.control.plug_charge
    assert isinstance(gate["session"], str)
    return gate["session"], gate["since"]


@pytest.mark.parametrize("outcome", ["loaded", "unload_rejected", "setup_skipped", "connection_changed"])
def test_profile_reload_keeps_only_the_same_running_service(tmp_path, monkeypatch, outcome):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "virtual_plug_charge": True})
        monkeypatch.setattr(integration, "PlugChargeObserver", lambda *_: Mock(async_stop=AsyncMock()))
        await _setup(hass, entry)
        old, host = entry.runtime_data, _host(hass, entry)
        token, since = await _plug_in(hass, entry, transport)

        async def reload(_entry_id):
            if outcome == "unload_rejected":
                hass.config_entries.unload_result = False
                assert not await integration.async_unload_entry(hass, entry)
                return
            await _unload(hass, entry)
            # The service and its heartbeat outlive the reload.
            assert _host(hass, entry) is host
            assert not old.client.connected
            if outcome == "setup_skipped":
                return
            if outcome == "connection_changed":
                entry.data = {**entry.data, "port": "/dev/other"}
            await _setup(hass, entry)
            assert entry.runtime_data.client is not old.client
            assert entry.runtime_data.control is not old.control
            if outcome == "loaded":
                assert _host(hass, entry) is host
                assert entry.runtime_data.control.plug_charge["session"] == token
                assert entry.runtime_data.control.plug_charge["since"] == since
            else:
                assert _host(hass, entry) is not host
                assert entry.runtime_data.control.plug_charge["session"] != token

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        # Unchanged options are a no-op vehicle switch; force the real reload.
        monkeypatch.setattr(integration, "_async_switch_vehicle", AsyncMock(return_value=False))
        try:
            await integration.async_reload_entry(hass, entry)
            if outcome == "setup_skipped":
                # A reload that loaded nothing stops the service it kept.
                assert _host(hass, entry) is None
            elif outcome == "unload_rejected":
                assert _host(hass, entry) is host
                assert old.client.connected
                assert old.control.plug_charge["session"] == token
        finally:
            hass.config_entries.unload_result = True
            if outcome != "setup_skipped":
                assert await integration.async_unload_entry(hass, entry)
            assert not entry.runtime_data.client.connected
            assert _host(hass, entry) is None

    asyncio.run(run())


def test_overlapping_profile_reloads_preserve_the_service_and_session(tmp_path, monkeypatch):
    async def run():
        hass = FakeHass(str(tmp_path))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "virtual_plug_charge": True})
        monkeypatch.setattr(integration, "PlugChargeObserver", lambda *_: Mock(async_stop=AsyncMock()))
        await _setup(hass, entry)
        host = _host(hass, entry)
        token, _since = await _plug_in(hass, entry, transport)
        started, finish = asyncio.Event(), asyncio.Event()
        owners = []

        async def reload(_entry_id):
            owners.append(entry.runtime_data)
            if len(owners) == 1:
                started.set()
                await finish.wait()
            await _unload(hass, entry)
            assert _host(hass, entry) is host
            await _setup(hass, entry)
            assert _host(hass, entry) is host
            assert entry.runtime_data.control.plug_charge["session"] == token

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        # Unchanged options are a no-op vehicle switch; force the real reload.
        monkeypatch.setattr(integration, "_async_switch_vehicle", AsyncMock(return_value=False))
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
            assert _host(hass, entry) is host
            assert transport.close_calls == 0
            assert entry.runtime_data.control.plug_charge["session"] == token
        finally:
            finish.set()
            await integration.async_unload_entry(hass, entry)
        assert _host(hass, entry) is None
        assert not transport.connected

    asyncio.run(run())
