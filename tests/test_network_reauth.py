"""Only definite TCP credential rejection starts HA reauth; settings stay bound."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.mennekes_amtron_rtu import _flow_gateway, config_flow
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronBusError,
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.data import DeviceIdentity
from custom_components.mennekes_amtron_rtu.device.waveshare_auth import (
    WaveshareAuthenticationError,
    WaveshareAuthenticationRejectedError,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeHass
from tests.test_config_flow import Flow
from tests.test_gateway_config_flow import _http_form
from tests.test_home_assistant_smoke import _boot
from tests.test_network_flow import NETWORK_INPUT
from tests.test_service_reauth import entity_ids

OLD_PASSWORD = "Old123"  # noqa: S105 - synthetic fixture
NEW_PASSWORD = "New123"  # noqa: S105 - synthetic fixture


@pytest.mark.parametrize("trigger", ["setup", "poll"])
@pytest.mark.parametrize("master", [False, True])
def test_definite_auth_rejection_starts_reauth_and_preserves_device_without_adapter_writes(monkeypatch, tmp_path, trigger, master):
    async def run():
        accepted = OLD_PASSWORD
        transports = []

        class AuthenticatedTransport(FakeModbusClient):
            def __init__(self, config, **_kwargs):
                super().__init__(device_bank())
                self.password = config.tcp_auth_password
                transports.append(self)

            async def connect(self):
                if self.password != accepted:
                    raise WaveshareAuthenticationRejectedError("credential rejected")
                return await super().connect()

        inspect = AsyncMock(side_effect=AssertionError("reauth changed adapter"))
        monkeypatch.setattr(config_flow, "inspect_gateway", inspect)
        monkeypatch.setattr(_flow_gateway, "configure_gateway", inspect)
        with patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", AuthenticatedTransport):
            hass = await _boot(tmp_path / "ha")
            try:
                manager = hass.config_entries.flow
                result = await manager.async_init(DOMAIN, context={"source": "user"})
                result = await manager.async_configure(result["flow_id"], {"next_step_id": "network"})
                result = await manager.async_configure(result["flow_id"], {**NETWORK_INPUT, "tcp_authentication": True})
                result = await manager.async_configure(result["flow_id"], {"tcp_auth_password": OLD_PASSWORD})
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                if master:
                    hass.config_entries.async_update_entry(entry, options={"control_mode": CONTROL_MODE_MASTER})
                    await hass.async_block_till_done()
                original = dict(entry.data)
                ids = entity_ids(hass, entry)
                previous = entry.runtime_data
                accepted = NEW_PASSWORD
                if trigger == "setup":
                    assert not await hass.config_entries.async_reload(entry.entry_id)
                    assert entry.state is ConfigEntryState.SETUP_ERROR
                else:
                    previous.client._client.close()
                    await previous.coordinator.async_refresh()
                    assert isinstance(previous.coordinator.last_exception, ConfigEntryAuthFailed)
                    if previous.heartbeat is not None:
                        assert not previous.heartbeat.running
                await hass.async_block_till_done()
                flows = manager.async_progress_by_handler(DOMAIN)
                assert len(flows) == 1
                assert flows[0]["step_id"] == "network_reauth"
                form = await manager.async_configure(flows[0]["flow_id"])
                _http_form(form)
                assert {marker.schema for marker in form["data_schema"].schema} == {"tcp_auth_password"}
                assert OLD_PASSWORD not in repr(form)
                rejected = await manager.async_configure(form["flow_id"], {"tcp_auth_password": "Wrong123"})
                assert rejected["errors"] == {"base": "gateway_authentication_failed"}
                assert dict(entry.data) == original
                result = await manager.async_configure(form["flow_id"], {"tcp_auth_password": NEW_PASSWORD})
                assert result["type"] == "abort"
                assert result["reason"] == "reauth_successful"
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert dict(entry.data) == {**original, "tcp_auth_password": NEW_PASSWORD}
                assert entity_ids(hass, entry) == ids
                assert entry.runtime_data.client.config.tcp_auth_password == NEW_PASSWORD
                if master:
                    assert entry.runtime_data.heartbeat.running
                inspect.assert_not_awaited()
                from custom_components.mennekes_amtron_rtu.device import registers as R
                assert all(address == R.HEARTBEAT.address for transport in transports for address, _ in transport.writes)
            finally:
                await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("trigger", ["setup", "poll"])
def test_transient_tcp_authentication_failure_remains_retryable_without_reauth(monkeypatch, tmp_path, trigger):
    async def run():
        fail = False

        class AuthenticatedTransport(FakeModbusClient):
            def __init__(self, *_args, **_kwargs):
                super().__init__(device_bank())

            async def connect(self):
                if fail:
                    raise WaveshareAuthenticationError("handshake timed out")
                return await super().connect()

        with patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", AuthenticatedTransport):
            hass = await _boot(tmp_path / "ha")
            try:
                manager = hass.config_entries.flow
                flow = await manager.async_init(DOMAIN, context={"source": "user"})
                flow = await manager.async_configure(flow["flow_id"], {"next_step_id": "network"})
                flow = await manager.async_configure(flow["flow_id"], {**NETWORK_INPUT, "tcp_authentication": True})
                await manager.async_configure(flow["flow_id"], {"tcp_auth_password": OLD_PASSWORD})
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                fail = True
                if trigger == "setup":
                    assert not await hass.config_entries.async_reload(entry.entry_id)
                    assert entry.state is ConfigEntryState.SETUP_RETRY
                else:
                    entry.runtime_data.client._client.close()
                    await entry.runtime_data.coordinator.async_refresh()
                    assert not entry.runtime_data.coordinator.last_update_success
                    assert not isinstance(entry.runtime_data.coordinator.last_exception, ConfigEntryAuthFailed)
                await hass.async_block_till_done()
                assert manager.async_progress_by_handler(DOMAIN) == []
            finally:
                await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize(("error", "expected"), [
    (ValueError("invalid"), "invalid_tcp_auth_password"),
    (AmtronConnectionError("offline"), "cannot_connect_network"),
    (AmtronBusError("no identity"), "invalid_network_response"),
])
def test_reauth_validation_keeps_form_and_never_replaces_entry_on_failure(monkeypatch, error, expected):
    async def run():
        flow = Flow(FakeHass())
        entry = flow.reconfigure_entry
        entry.data = {**NETWORK_INPUT, "tcp_authentication": True, "tcp_auth_password": OLD_PASSWORD}
        flow._get_reauth_entry = lambda: entry
        original = dict(entry.data)
        monkeypatch.setattr(config_flow, "async_test_connection", AsyncMock(side_effect=error))
        result = await flow.async_step_network_reauth({"tcp_auth_password": NEW_PASSWORD})
        assert expected in result["errors"].values()
        assert entry.data == original
        _http_form(result)
    asyncio.run(run())


def test_reauth_does_not_probe_if_old_connection_cannot_be_released(monkeypatch):
    async def run():
        hass = FakeHass()
        flow = Flow(hass)
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.LOADED
        entry.data = {**NETWORK_INPUT, "tcp_authentication": True, "tcp_auth_password": OLD_PASSWORD}
        flow._get_reauth_entry = lambda: entry
        hass.config_entries.async_unload = AsyncMock(return_value=False)
        probe = AsyncMock()
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        result = await flow.async_step_network_reauth({"tcp_auth_password": NEW_PASSWORD})
        assert result["errors"] == {"base": "cannot_connect_network"}
        probe.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["identity", "missing_identity", "endpoint"])
def test_reauth_never_saves_credentials_for_a_different_wallbox_or_changed_endpoint(monkeypatch, change):
    async def run():
        hass = FakeHass()
        flow = Flow(hass)
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.LOADED
        entry.data = {**NETWORK_INPUT, "tcp_authentication": True, "tcp_auth_password": OLD_PASSWORD}
        flow._get_reauth_entry = lambda: entry

        async def unload(_entry_id, *, _lock=True):
            entry.state = ConfigEntryState.NOT_LOADED
            return True

        hass.config_entries.async_unload = AsyncMock(side_effect=unload)
        hass.config_entries.async_setup = AsyncMock()
        flow.async_abort = lambda **kwargs: {"type": "abort", **kwargs}

        async def probe(config):
            assert config.tcp_auth_password == NEW_PASSWORD
            if change == "endpoint":
                entry.data = {**entry.data, "host": "changed.invalid"}
            return DeviceIdentity(serial_number=None if change == "missing_identity" else "OTHER")

        def mismatch():
            if change != "endpoint":
                raise AbortFlow("unique_id_mismatch")

        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        flow._abort_if_unique_id_mismatch = mismatch
        if change == "endpoint":
            result = await flow.async_step_network_reauth({"tcp_auth_password": NEW_PASSWORD})
            assert result["reason"] == "connection_settings_changed"
            assert entry.data["host"] == "changed.invalid"
            hass.config_entries.async_setup.assert_not_awaited()
        else:
            with pytest.raises(AbortFlow):
                await flow.async_step_network_reauth({"tcp_auth_password": NEW_PASSWORD})
            hass.config_entries.async_setup.assert_awaited_once_with(entry.entry_id, _lock=False)
        assert entry.data["tcp_auth_password"] == OLD_PASSWORD
    asyncio.run(run())


@pytest.mark.parametrize("probe_outcome", ["success", "connection_error"])
def test_inflight_network_reauth_yields_to_a_changed_entry(monkeypatch, tmp_path, probe_outcome):
    async def run():
        connections = []

        class AuthenticatedTransport(FakeModbusClient):
            def __init__(self, config, **_kwargs):
                super().__init__(device_bank())
                connections.append((config.host, config.tcp_auth_password))

        started, release = asyncio.Event(), asyncio.Event()
        task = None
        with patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", AuthenticatedTransport):
            hass = await _boot(tmp_path / "ha")
            try:
                manager = hass.config_entries.flow
                flow = await manager.async_init(DOMAIN, context={"source": "user"})
                flow = await manager.async_configure(flow["flow_id"], {"next_step_id": "network"})
                flow = await manager.async_configure(flow["flow_id"], {**NETWORK_INPUT, "tcp_authentication": True})
                result = await manager.async_configure(flow["flow_id"], {"tcp_auth_password": OLD_PASSWORD})
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]

                async def probe(config):
                    assert config.host == NETWORK_INPUT["host"]
                    assert config.tcp_auth_password == NEW_PASSWORD
                    started.set()
                    await release.wait()
                    if probe_outcome == "connection_error":
                        raise AmtronConnectionError("temporary outage")
                    return DeviceIdentity(serial_number=entry.unique_id)

                monkeypatch.setattr(config_flow, "async_test_connection", probe)
                flow = await manager.async_init(
                    DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=dict(entry.data),
                )
                assert flow["step_id"] == "network_reauth"
                task = asyncio.create_task(manager.async_configure(
                    flow["flow_id"], {"tcp_auth_password": NEW_PASSWORD},
                ))
                await asyncio.wait_for(started.wait(), 5)
                assert entry.state is ConfigEntryState.NOT_LOADED
                replacement = {**entry.data, "host": "new-gateway.local", "tcp_auth_password": "Other123"}
                hass.config_entries.async_update_entry(entry, data=replacement)
                setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
                await asyncio.sleep(0)
                assert not setup.done()

                release.set()
                result = await task
                assert await setup
                await hass.async_block_till_done()
                assert result["type"] == "abort"
                assert result["reason"] == "connection_settings_changed"
                assert dict(entry.data) == replacement
                assert entry.state is ConfigEntryState.LOADED
                assert ("new-gateway.local", NEW_PASSWORD) not in connections
                assert connections[-1] == ("new-gateway.local", "Other123")
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_network_reauth_waiting_for_setup_lock_does_not_probe_a_newly_loaded_entry(monkeypatch):
    async def run():
        hass = FakeHass()
        flow = Flow(hass)
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.NOT_LOADED
        entry.data = {**NETWORK_INPUT, "tcp_authentication": True, "tcp_auth_password": OLD_PASSWORD}
        flow._get_reauth_entry = lambda: entry
        flow.async_abort = lambda **kwargs: {"type": "abort", **kwargs}
        probe = AsyncMock()
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        await entry.setup_lock.acquire()
        try:
            task = asyncio.create_task(flow.async_step_network_reauth({"tcp_auth_password": NEW_PASSWORD}))
            await asyncio.sleep(0)
            entry.state = ConfigEntryState.LOADED
        finally:
            entry.setup_lock.release()
        assert await task == {"type": "abort", "reason": "connection_settings_changed"}
        probe.assert_not_awaited()

    asyncio.run(asyncio.wait_for(run(), 5))
