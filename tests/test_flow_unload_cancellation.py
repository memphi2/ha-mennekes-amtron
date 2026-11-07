"""A cancelled connection flow must finish HA's in-progress unload."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot
from tests.test_network_flow import NETWORK_INPUT
from tests.test_soc_goal_ha_lifecycle import _entry


@pytest.mark.parametrize("operation", ["probe", "search", "reauth"])
@pytest.mark.parametrize("entry_changed", [False, True])
def test_cancellation_finishes_unload_and_preserves_setup_ownership(
    tmp_path, monkeypatch, operation, entry_changed,
):
    async def run():
        bus = FakeModbusClient(device_bank())

        class NetworkTransport(FakeModbusClient):
            def __init__(self, *_args, **_kwargs):
                super().__init__(device_bank())

        release = asyncio.Event()
        started = asyncio.Event()
        restore_started = asyncio.Event()
        restore_release = asyncio.Event()
        task = None
        with (
            patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=bus),
            patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", NetworkTransport),
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                manager = hass.config_entries.flow
                if operation == "reauth":
                    flow = await manager.async_init(DOMAIN, context={"source": "user"})
                    flow = await manager.async_configure(flow["flow_id"], {"next_step_id": "network"})
                    flow = await manager.async_configure(
                        flow["flow_id"], {**NETWORK_INPUT, "tcp_authentication": True},
                    )
                    await manager.async_configure(flow["flow_id"], {"tcp_auth_password": "Old123"})
                    await hass.async_block_till_done()
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                else:
                    entry = await _entry(hass)
                assert entry.state is ConfigEntryState.LOADED
                original = dict(entry.data)
                previous_runtime = entry.runtime_data

                original_unload = hass.config_entries.async_unload_platforms
                unload_count = 0

                async def delayed_unload(*args, **kwargs):
                    nonlocal unload_count
                    unload_count += 1
                    if unload_count == (2 if operation == "search" else 1):
                        started.set()
                        await release.wait()
                    return await original_unload(*args, **kwargs)

                monkeypatch.setattr(hass.config_entries, "async_unload_platforms", delayed_unload)
                if operation == "reauth":
                    flow = await manager.async_init(
                        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id},
                        data=original,
                    )
                    task = asyncio.create_task(manager.async_configure(
                        flow["flow_id"], {"tcp_auth_password": "New123"},
                    ))
                else:
                    flow = await manager.async_init(
                        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id},
                    )
                    flow = await manager.async_configure(
                        flow["flow_id"], {"next_step_id": "serial_reconfigure"},
                    )
                    values = {
                        "port": "/dev/changed", "device_id": 50, "baudrate": 57600,
                        "frame": "8n2", "search": "addresses" if operation == "search" else "off",
                    }
                    if operation == "search":
                        monkeypatch.setattr(
                            config_flow, "async_test_connection",
                            AsyncMock(side_effect=AmtronConnectionError("probe failed")),
                        )
                        result = await manager.async_configure(flow["flow_id"], values)
                        assert result["type"] == "progress"
                        task = manager._progress[flow["flow_id"]].async_get_progress_task()
                    else:
                        task = asyncio.create_task(manager.async_configure(flow["flow_id"], values))

                await asyncio.wait_for(started.wait(), 5)
                assert entry.state is ConfigEntryState.UNLOAD_IN_PROGRESS
                if not entry_changed:
                    original_setup = hass.config_entries.async_setup

                    async def delayed_restore(*args, **kwargs):
                        restore_started.set()
                        await restore_release.wait()
                        return await original_setup(*args, **kwargs)

                    monkeypatch.setattr(hass.config_entries, "async_setup", delayed_restore)
                if entry_changed:
                    key, value = (
                        ("host", "replacement-gateway.local") if operation == "reauth"
                        else ("port", "/dev/replacement")
                    )
                    replacement = {**original, key: value}
                    hass.config_entries.async_update_entry(entry, data=replacement)
                else:
                    replacement = original
                if operation == "search":
                    # HA cancels the progress task when the user aborts the flow.
                    manager.async_abort(flow["flow_id"])
                else:
                    task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert entry.setup_lock.locked()
                # A second cancellation must not interrupt lifecycle cleanup.
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                release.set()
                if not entry_changed:
                    await asyncio.wait_for(restore_started.wait(), 5)
                    assert entry.state is ConfigEntryState.NOT_LOADED
                    task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done()
                    assert entry.setup_lock.locked()
                    restore_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert dict(entry.data) == replacement
                assert entry.runtime_data is not previous_runtime
                assert entry.runtime_data.client.connected
                assert not bus.writes
            finally:
                release.set()
                restore_release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("operation", ["probe", "search", "reauth"])
@pytest.mark.parametrize("cancel_first", ["probe", "restore"])
def test_cancel_during_restoration_finishes_ha_setup(
    tmp_path, monkeypatch, operation, cancel_first,
):
    async def run():
        bus = FakeModbusClient(device_bank())

        class NetworkTransport(FakeModbusClient):
            def __init__(self, *_args, **_kwargs):
                super().__init__(device_bank())

        probe_started = asyncio.Event()
        probe_release = asyncio.Event()
        restore_started = asyncio.Event()
        restore_release = asyncio.Event()
        task = None
        with (
            patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=bus),
            patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", NetworkTransport),
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                manager = hass.config_entries.flow
                if operation == "reauth":
                    flow = await manager.async_init(DOMAIN, context={"source": "user"})
                    flow = await manager.async_configure(flow["flow_id"], {"next_step_id": "network"})
                    flow = await manager.async_configure(
                        flow["flow_id"], {**NETWORK_INPUT, "tcp_authentication": True},
                    )
                    await manager.async_configure(flow["flow_id"], {"tcp_auth_password": "Old123"})
                    await hass.async_block_till_done()
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                else:
                    entry = await _entry(hass)
                original_data = dict(entry.data)
                original_setup = integration.async_setup_entry
                setups = 0

                async def delayed_setup(hass, entry):
                    nonlocal setups
                    setups += 1
                    # Search restores the first failed probe before starting.
                    if setups == (2 if operation == "search" else 1):
                        restore_started.set()
                        await restore_release.wait()
                    return await original_setup(hass, entry)

                async def probe(*_args, **_kwargs):
                    probe_started.set()
                    if cancel_first == "probe":
                        await probe_release.wait()
                    if operation != "search":
                        raise AmtronConnectionError("probe failed")

                monkeypatch.setattr(integration, "async_setup_entry", delayed_setup)
                if operation == "reauth":
                    monkeypatch.setattr(config_flow, "async_test_connection", probe)
                    flow = await manager.async_init(
                        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id},
                        data=original_data,
                    )
                    task = asyncio.create_task(manager.async_configure(
                        flow["flow_id"], {"tcp_auth_password": "New123"},
                    ))
                else:
                    flow = await manager.async_init(
                        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id},
                    )
                    flow = await manager.async_configure(
                        flow["flow_id"], {"next_step_id": "serial_reconfigure"},
                    )
                    values = {
                        "port": "/dev/changed", "device_id": 50, "baudrate": 57600,
                        "frame": "8n2", "search": "addresses" if operation == "search" else "off",
                    }
                    if operation == "search":
                        monkeypatch.setattr(
                            config_flow, "async_test_connection",
                            AsyncMock(side_effect=AmtronConnectionError("probe failed")),
                        )
                        monkeypatch.setattr(config_flow, "async_search", probe)
                        result = await manager.async_configure(flow["flow_id"], values)
                        assert result["type"] == "progress"
                        task = manager._progress[flow["flow_id"]].async_get_progress_task()
                    else:
                        monkeypatch.setattr(config_flow, "async_test_connection", probe)
                        task = asyncio.create_task(manager.async_configure(flow["flow_id"], values))

                await asyncio.wait_for(probe_started.wait(), 5)
                if cancel_first == "probe":
                    if operation == "search":
                        manager.async_abort(flow["flow_id"])
                    else:
                        task.cancel()
                await asyncio.wait_for(restore_started.wait(), 5)
                assert entry.state is ConfigEntryState.SETUP_IN_PROGRESS
                if operation == "search" and cancel_first == "restore":
                    manager.async_abort(flow["flow_id"])
                else:
                    task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert entry.setup_lock.locked()
                restore_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert dict(entry.data) == original_data
                assert entry.runtime_data.client.connected
                assert not bus.writes
            finally:
                probe_release.set()
                restore_release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
