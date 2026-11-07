"""Connection probes must not overwrite or interrupt a newer entry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import SerialConfig
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeHass
from tests.test_config_flow import IDENTITY, Flow
from tests.test_gateway_config_flow import SETTINGS, _mocks
from tests.test_home_assistant_smoke import _boot
from tests.test_network_flow import NETWORK_INPUT
from tests.test_soc_goal_ha_lifecycle import _entry


@pytest.mark.parametrize("transport", ["serial", "network", "adapter"])
@pytest.mark.parametrize("probe_outcome", ["success", "failure"])
def test_reconfigure_probe_yields_to_a_changed_entry(monkeypatch, tmp_path, transport, probe_outcome):
    async def run():
        bus = FakeModbusClient(device_bank())
        started, release = asyncio.Event(), asyncio.Event()
        task = setup = None
        receipt = None
        if transport == "adapter":
            _inspect, _configure, _probe, receipt, _events = _mocks(monkeypatch)

        async def probe(_config):
            started.set()
            await release.wait()
            if probe_outcome == "failure":
                raise AmtronConnectionError("temporary outage")
            return IDENTITY

        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        with patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=bus):
            hass = await _boot(tmp_path / "ha")
            try:
                # Initial setup uses the physical-client fake, not the delayed probe.
                with patch.object(config_flow, "async_test_connection", return_value=IDENTITY):
                    entry = await _entry(hass)
                manager = hass.config_entries.flow
                flow = await manager.async_init(DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id})
                step = "serial_reconfigure" if transport == "serial" else "network_reconfigure"
                flow = await manager.async_configure(flow["flow_id"], {"next_step_id": step})
                if transport == "serial":
                    values = {"port": "/dev/other", "device_id": 50, "baudrate": 57600, "frame": "8n2", "search": "off"}
                else:
                    values = {**NETWORK_INPUT, "configure_gateway": transport == "adapter"}
                if transport == "adapter":
                    flow = await manager.async_configure(flow["flow_id"], values)
                    values = SETTINGS
                task = asyncio.create_task(manager.async_configure(flow["flow_id"], values))
                await asyncio.wait_for(started.wait(), 5)
                assert entry.state is ConfigEntryState.NOT_LOADED

                replacement = {**entry.data, "baudrate": 19200}
                hass.config_entries.async_update_entry(entry, data=replacement)
                setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
                await asyncio.sleep(0)
                assert not setup.done()
                release.set()
                result = await task
                assert await setup
                await hass.async_block_till_done()
                if transport == "adapter" and probe_outcome == "failure":
                    # Existing rollback protection reports an ambiguous adapter
                    # state when another actor also changed the entry.
                    assert result["errors"] == {"base": "gateway_rollback_failed"}
                else:
                    assert result["type"] == "abort"
                    assert result["reason"] == "connection_settings_changed"
                assert dict(entry.data) == replacement
                assert entry.state is ConfigEntryState.LOADED
                if receipt is not None:
                    assert not receipt.committed
                assert not bus.writes
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                if setup is not None:
                    await asyncio.gather(setup, return_exceptions=True)
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_search_does_not_restore_an_entry_changed_while_scanning(monkeypatch, outcome):
    async def run():
        hass = FakeHass()
        flow = Flow(hass)
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.LOADED
        flow._pending_step = "serial_reconfigure"
        started, release = asyncio.Event(), asyncio.Event()

        async def unload(_entry_id, *, _lock=True):
            entry.state = ConfigEntryState.NOT_LOADED
            return True

        async def search(*_args, **_kwargs):
            started.set()
            await release.wait()
            if outcome == "failure":
                raise AmtronConnectionError("temporary outage")

        hass.config_entries.async_unload = AsyncMock(side_effect=unload)
        hass.config_entries.async_setup = AsyncMock()
        monkeypatch.setattr(config_flow, "async_search", search)
        task = asyncio.create_task(flow._async_search_bus(SerialConfig("/dev/other"), False))
        await asyncio.wait_for(started.wait(), 5)
        entry.data = {**entry.data, "port": "/dev/replacement"}
        release.set()
        with pytest.raises(config_flow._ConnectionSettingsChangedError):
            await task
        hass.config_entries.async_setup.assert_not_awaited()

    asyncio.run(asyncio.wait_for(run(), 10))


def test_app_reauth_discovery_does_not_follow_a_changed_entry(monkeypatch):
    async def run():
        flow = Flow(FakeHass())
        entry = flow.reconfigure_entry
        entry.data = {"server_url": "http://local-mennekes-amtron-modbus:8765", "server_token": "old"}
        flow._get_reauth_entry = lambda: entry
        flow.async_abort = lambda **kwargs: {"type": "abort", **kwargs}
        started, release = asyncio.Event(), asyncio.Event()

        async def discover(_hass):
            started.set()
            await release.wait()
            return []

        monkeypatch.setattr(config_flow, "async_app_discoveries", discover)
        task = asyncio.create_task(flow.async_step_reauth_app())
        await asyncio.wait_for(started.wait(), 5)
        entry.data = {"port": "/dev/replacement"}
        release.set()
        assert await task == {"type": "abort", "reason": "connection_settings_changed"}

    asyncio.run(asyncio.wait_for(run(), 10))
