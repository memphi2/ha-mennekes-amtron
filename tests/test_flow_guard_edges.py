"""Concurrent connection changes must never commit stale flow decisions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import SerialConfig
from custom_components.mennekes_amtron_rtu.device.waveshare_gateway import GatewayError
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.ha_fakes import FakeHass
from tests.test_config_flow import IDENTITY, SearchFlow
from tests.test_gateway_config_flow import SNAPSHOT
from tests.test_network_flow import NETWORK_INPUT
from tests.test_service_discovery import URL, discovery_info


def _flow():
    flow = SearchFlow(FakeHass())
    flow.context = {"source": "reconfigure"}
    flow._reconfigure_data = dict(flow.reconfigure_entry.data)
    flow.async_abort = lambda **kwargs: {"type": "abort", **kwargs}
    return flow


def _change(flow):
    flow.reconfigure_entry.data = {**flow.reconfigure_entry.data, "port": "/dev/other"}


def _assert_changed(result):
    assert result == {"type": "abort", "reason": "connection_settings_changed"}


@pytest.mark.parametrize("step", [
    "network", "server", "hassio", "hassio_confirm", "serial", "network_auth", "network_adapter", "search_result",
])
def test_stale_reconfigure_forms_never_apply_old_settings(step):
    async def run():
        flow = _flow()
        flow._pending_step = "serial_reconfigure" if step == "search_result" else "network_reconfigure"
        flow._pending_input = dict(NETWORK_INPUT)
        flow._gateway_snapshot = SNAPSHOT
        _change(flow)
        if step == "network":
            result = await flow.async_step_network_reconfigure()
        elif step == "server":
            result = await flow.async_step_server()
        elif step == "hassio":
            result = await flow.async_step_hassio(discovery_info())
        elif step == "hassio_confirm":
            result = await flow.async_step_hassio_confirm()
        elif step == "serial":
            result = await flow.async_step_serial_reconfigure()
        elif step == "network_auth":
            result = await flow.async_step_network_auth()
        elif step == "network_adapter":
            result = await flow.async_step_network_adapter()
        else:
            result = await flow.async_step_search_result()
        _assert_changed(result)
        assert flow.updates is None

    asyncio.run(run())


def test_unchanged_reconfigure_can_ask_for_network_credentials():
    async def run():
        flow = _flow()
        flow._pending_step = "network_reconfigure"
        flow._pending_input = dict(NETWORK_INPUT)
        result = await flow.async_step_network_auth()
        assert result["type"] == "form"
        assert result["step_id"] == "network_auth"

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "gateway_error"])
def test_adapter_inspection_detects_a_concurrent_connection_change(monkeypatch, outcome):
    async def run():
        flow = _flow()

        async def inspect(_host):
            _change(flow)
            if outcome == "gateway_error":
                raise GatewayError("gateway_unreachable")
            return SNAPSHOT

        monkeypatch.setattr(config_flow, "inspect_gateway", inspect)
        result = await flow.async_step_network_reconfigure({**NETWORK_INPUT, "configure_gateway": True})
        _assert_changed(result)
        assert flow.updates is None

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["service_discovery", "serial_ports"])
def test_form_discovery_does_not_display_stale_connection_settings(monkeypatch, stage):
    async def run():
        flow = _flow()

        async def discover(_hass):
            _change(flow)
            return []

        async def ports():
            _change(flow)
            return []

        if stage == "service_discovery":
            monkeypatch.setattr(config_flow, "async_app_discoveries", discover)
            result = await flow.async_step_server()
        else:
            flow._async_port_options = ports
            result = await flow.async_step_serial_reconfigure()
        _assert_changed(result)

    asyncio.run(run())


def test_service_identity_lookup_cannot_commit_an_entry_changed_during_lookup(monkeypatch):
    async def run():
        flow = _flow()
        client = SimpleNamespace(identity=IDENTITY, async_connect=AsyncMock(), async_close=AsyncMock())
        monkeypatch.setattr(config_flow, "async_get_clientsession", lambda _hass: None)
        monkeypatch.setattr(config_flow, "RemoteClient", lambda *_args: client)

        async def set_unique(_serial):
            _change(flow)

        flow.async_set_unique_id = set_unique
        result = await flow.async_step_server({"server_url": URL, "server_token": "x" * 32})
        _assert_changed(result)
        client.async_close.assert_awaited_once()
        assert flow.updates is None

    asyncio.run(run())


def test_app_discovery_identity_lookup_does_not_follow_an_updated_entry():
    async def run():
        flow = _flow()
        flow._async_in_progress = lambda **_kwargs: []

        async def set_unique(_unique):
            _change(flow)

        flow.async_set_unique_id = set_unique
        _assert_changed(await flow.async_step_hassio(discovery_info()))
        assert flow.updates is None

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["discovery", "connect", "identity", "auth_error", "offline"])
def test_app_confirmation_rejects_a_connection_changed_at_any_async_stage(monkeypatch, stage):
    async def run():
        flow = _flow()
        info = discovery_info()
        flow.context["title_placeholders"] = {"app": info.slug}
        flow._discovered_service = config_flow._discovered_connection(info)
        client = SimpleNamespace(identity=IDENTITY, async_close=AsyncMock())

        async def discover(_hass):
            if stage == "discovery":
                _change(flow)
            return [info]

        async def connect():
            if stage in ("connect", "auth_error", "offline"):
                _change(flow)
            if stage == "auth_error":
                raise ServiceConnectionError("service_auth_failed", "rejected")
            if stage == "offline":
                raise AmtronConnectionError("offline")

        async def set_unique(_serial):
            if stage == "identity":
                _change(flow)

        client.async_connect = AsyncMock(side_effect=connect)
        flow.async_set_unique_id = set_unique
        monkeypatch.setattr(config_flow, "async_app_discoveries", discover)
        monkeypatch.setattr(config_flow, "async_get_clientsession", lambda _hass: None)
        monkeypatch.setattr(config_flow, "RemoteClient", lambda *_args: client)
        result = await flow.async_step_hassio_confirm({})
        _assert_changed(result)
        if stage != "discovery":
            client.async_close.assert_awaited_once()
        assert flow.updates is None

    asyncio.run(run())


def test_waiting_reconfigure_does_not_unload_a_newly_started_runtime(monkeypatch):
    async def run():
        flow = _flow()
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.NOT_LOADED
        config = SerialConfig("/dev/other")
        probe = AsyncMock()
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        await entry.setup_lock.acquire()
        try:
            task = asyncio.create_task(flow._async_test_and_finish("serial_reconfigure", {}, config))
            await asyncio.sleep(0)
            entry.state = ConfigEntryState.LOADED
        finally:
            entry.setup_lock.release()
        _assert_changed(await task)
        probe.assert_not_awaited()

    asyncio.run(run())


def test_waiting_search_does_not_unload_a_newly_started_runtime(monkeypatch):
    async def run():
        flow = _flow()
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.NOT_LOADED
        flow._pending_step = "serial_reconfigure"
        search = AsyncMock()
        monkeypatch.setattr(config_flow, "async_search", search)
        await entry.setup_lock.acquire()
        try:
            task = asyncio.create_task(flow._async_search_bus(SerialConfig("/dev/other"), False))
            await asyncio.sleep(0)
            entry.state = ConfigEntryState.LOADED
        finally:
            entry.setup_lock.release()
        with pytest.raises(config_flow._ConnectionSettingsChangedError):
            await task
        search.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("entry_changed", [False, True])
def test_unchanged_connection_probe_failure_preserves_concurrent_updates(monkeypatch, entry_changed):
    async def run():
        flow = _flow()
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.LOADED
        config = SerialConfig("/dev/fake")
        entry.runtime_data = SimpleNamespace(client=SimpleNamespace(config=config))

        async def identity(_client):
            if entry_changed:
                _change(flow)
            raise AmtronConnectionError("offline")

        monkeypatch.setattr(config_flow, "async_read_identity", identity)
        if entry_changed:
            _assert_changed(await flow._async_test_and_finish("serial_reconfigure", {}, config))
        else:
            with pytest.raises(AmtronConnectionError):
                await flow._async_test_and_finish("serial_reconfigure", {}, config)

    asyncio.run(run())


def test_cancelled_probe_never_restores_an_entry_updated_during_the_probe(monkeypatch):
    async def run():
        flow = _flow()
        entry = flow.reconfigure_entry
        entry.state = ConfigEntryState.LOADED
        entry.runtime_data = SimpleNamespace(client=SimpleNamespace(config=SerialConfig("/dev/fake")))

        async def unload(_entry, _original):
            entry.state = ConfigEntryState.NOT_LOADED
            return True

        async def probe(_config):
            _change(flow)
            raise asyncio.CancelledError

        flow._async_unload_for_probe = unload
        flow._async_restore_after_probe = AsyncMock()
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        with pytest.raises(asyncio.CancelledError):
            await flow._async_test_and_finish("serial_reconfigure", {}, SerialConfig("/dev/other"))
        flow._async_restore_after_probe.assert_not_awaited()

    asyncio.run(run())


def test_search_conflict_is_reported_and_a_unchanged_result_remains_accessible():
    async def run():
        flow = _flow()
        flow._pending_step = "serial_reconfigure"

        async def conflict():
            raise config_flow._ConnectionSettingsChangedError

        flow._search_task = asyncio.create_task(conflict())
        await asyncio.sleep(0)
        _assert_changed(await flow.async_step_search())
        assert flow._search_task is None
        flow._search_result = None
        flow._pending_input = {"port": "/dev/fake"}
        flow._async_port_options = AsyncMock(return_value=[])
        result = await flow.async_step_search_result()
        assert result["type"] == "form"
        assert result["step_id"] == "serial_reconfigure"

    asyncio.run(run())
