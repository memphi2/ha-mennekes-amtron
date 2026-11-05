"""Direct LAN setup, identity checks and transport handover in Home Assistant."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import probatio
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import AbortFlow

from custom_components.mennekes_amtron_rtu import _flow_connect, config_flow
from custom_components.mennekes_amtron_rtu.config_schemas import network_schema
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronBusError,
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONTROL_MODE_MASTER,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_config_flow import IDENTITY, USER_INPUT, Flow, _suggested

NETWORK_INPUT = {
    "connection_type": "rtu_over_tcp",
    "host": "wallbox-gateway.local",
    "tcp_port": 502,
    "device_id": 50,
}


def test_network_schema_exposes_only_gateway_fields():
    schema = network_schema()
    assert {marker.schema for marker in schema.schema} == set(NETWORK_INPUT)
    assert schema({"host": "wallbox-gateway.local"}) == NETWORK_INPUT
    protocol = next(
        selector for marker, selector in schema.schema.items()
        if marker.schema == "connection_type"
    )
    assert protocol.config["options"] == ["rtu_over_tcp", "modbus_tcp"]


def test_network_schema_accepts_integral_ui_numbers():
    data = network_schema()({**NETWORK_INPUT, "tcp_port": 502.0, "device_id": 50.0})
    assert data == NETWORK_INPUT
    assert type(data["tcp_port"]) is int
    assert type(data["device_id"]) is int


@pytest.mark.parametrize("field", ["device_id", "tcp_port"])
@pytest.mark.parametrize("value", [True, 25.5, "25", float("nan"), float("inf")])
def test_network_schema_rejects_non_integer_identifiers(field, value):
    with pytest.raises(probatio.Invalid):
        network_schema()({**NETWORK_INPUT, field: value})


@pytest.mark.parametrize("step", ["user", "reconfigure"])
def test_transport_menus_include_direct_network(step):
    async def run():
        flow = Flow(FakeHass())
        flow.async_show_menu = lambda **result: result
        result = await getattr(flow, f"async_step_{step}")()
        assert result["menu_options"] == (
            ["server", "serial", "network"] if step == "user"
            else ["server", "serial_reconfigure", "network_reconfigure"]
        )

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["rtu_over_tcp", "modbus_tcp"])
def test_network_setup_normalizes_the_endpoint_and_starts_read_only(monkeypatch, protocol):
    async def run():
        probe = AsyncMock(return_value=IDENTITY)
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        flow = Flow(FakeHass())
        result = await flow.async_step_network({
            **NETWORK_INPUT, "host": "WALLBOX-GATEWAY.local", "connection_type": protocol
        })
        assert result["type"] == "create_entry"
        assert result["data"] == {
            **NETWORK_INPUT, "connection_type": protocol,
            "serial_number": IDENTITY.serial_number,
        }
        assert result.get("options", {}) == {}
        assert flow.set_unique_id == IDENTITY.serial_number
        assert flow.aborted == ["configured"]
        assert probe.await_args.args == (NetworkConfig(
            host=NETWORK_INPUT["host"], connection_type=protocol
        ),)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("error", "expected"),
    [(AmtronConnectionError("timeout"), "cannot_connect_network"),
     (AmtronBusError("invalid layout"), "invalid_network_response")],
)
def test_network_failures_preserve_inputs_without_searching_serial(monkeypatch, error, expected):
    async def run():
        probe = AsyncMock(side_effect=error)
        search = AsyncMock(side_effect=AssertionError("network cannot search serial"))
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        monkeypatch.setattr(config_flow, "async_search", search)
        flow = Flow(FakeHass())
        flow._async_port_options = search
        result = await flow.async_step_network(dict(NETWORK_INPUT))
        assert result["errors"] == {"base": expected}
        assert result["description_placeholders"] == {"validated_layout": "v01.03"}
        assert _suggested(result) == NETWORK_INPUT
        search.assert_not_awaited()
        probe.side_effect = None
        probe.return_value = IDENTITY
        result = await flow.async_step_network(dict(NETWORK_INPUT))
        assert result["type"] == "create_entry"
        assert flow.set_unique_id == IDENTITY.serial_number

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [
    {"host": "http://wallbox.local"},
    {"host": "user:password@wallbox.local"},
    {"tcp_port": 0},
    {"tcp_port": True},
    {"device_id": 9},
    {"connection_type": "serial"},
])
def test_invalid_network_settings_do_not_interrupt_the_existing_connection(monkeypatch, invalid):
    async def run():
        hass = FakeHass()
        hass.config_entries.async_unload = AsyncMock()
        probe = AsyncMock()
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        flow = Flow(hass)
        flow.reconfigure_entry.state = ConfigEntryState.LOADED
        result = await flow.async_step_network_reconfigure({**NETWORK_INPUT, **invalid})
        assert result["errors"] == {"base": "invalid_network_config"}
        hass.config_entries.async_unload.assert_not_awaited()
        probe.assert_not_awaited()

    asyncio.run(run())


def test_network_reconfigure_prefills_the_current_gateway():
    async def run():
        flow = Flow(FakeHass())
        flow.reconfigure_entry.data = {
            **NETWORK_INPUT, "connection_type": "modbus_tcp", "tcp_port": 1502,
            "device_id": 23,
        }
        result = await flow.async_step_network_reconfigure()
        assert _suggested(result) == flow.reconfigure_entry.data

    asyncio.run(run())


@pytest.mark.parametrize("data", [
    {"port": "/dev/fake", "connection_type": "serial"},
    {"port": "/dev/fake"},
    {"server_url": "http://service:8765", "server_token": "private"},
])
def test_network_reconfigure_from_another_backend_defaults_to_a_network_protocol(data):
    async def run():
        flow = Flow(FakeHass())
        flow.reconfigure_entry.data = data
        result = await flow.async_step_network_reconfigure()
        assert "connection_type" not in _suggested(result)
        assert result["data_schema"]({"host": "wallbox-gateway.local"}) == NETWORK_INPUT

    asyncio.run(run())


@pytest.mark.parametrize("source", ["serial", "rtu_over_tcp", "modbus_tcp", "service"])
@pytest.mark.parametrize("target", ["serial", "rtu_over_tcp", "modbus_tcp"])
def test_reconfiguration_replaces_backend_fields_and_preserves_options(monkeypatch, source, target):
    async def run():
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, CONF_CURRENT_LIMIT: 8})
        entry.state = ConfigEntryState.LOADED
        source_data = {
            "serial": {"port": "/dev/old", "connection_type": "serial"},
            "service": {"server_url": "http://service:8765", "server_token": "private"},
            "rtu_over_tcp": {**NETWORK_INPUT, "host": "old-gateway.local"},
            "modbus_tcp": {**NETWORK_INPUT, "host": "old-gateway.local", "connection_type": "modbus_tcp"},
        }[source]
        entry.data = {**source_data, "serial_number": IDENTITY.serial_number}
        entry.runtime_data = SimpleNamespace(client=SimpleNamespace(config=object()))
        events = []

        async def unload(entry_id):
            assert entry_id == entry.entry_id
            events.append("unload")
            entry.state = ConfigEntryState.NOT_LOADED
            return True

        async def probe(config):
            assert entry.state is ConfigEntryState.NOT_LOADED
            events.append("probe")
            return IDENTITY

        hass.config_entries.async_unload = unload
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        flow = Flow(hass)
        flow.reconfigure_entry = entry
        result = (
            await flow.async_step_serial_reconfigure(dict(USER_INPUT)) if target == "serial"
            else await flow.async_step_network_reconfigure({**NETWORK_INPUT, "connection_type": target})
        )
        assert result["reason"] == "reconfigure_successful"
        assert events == ["unload", "probe"]
        assert flow.aborted == ["mismatch"]
        assert flow.set_unique_id == IDENTITY.serial_number
        assert flow.updates["connection_type"] == target
        assert flow.updates["serial_number"] == IDENTITY.serial_number
        assert "server_url" not in flow.updates
        assert "server_token" not in flow.updates
        if target == "serial":
            assert "host" not in flow.updates
            assert "tcp_port" not in flow.updates
        else:
            assert "port" not in flow.updates
            assert "baudrate" not in flow.updates
        assert entry.options == {CONF_CONTROL_MODE: CONTROL_MODE_MASTER, CONF_CURRENT_LIMIT: 8}

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "cancel", "wrong_device", "unload"])
def test_failed_network_handover_restores_the_loaded_entry(monkeypatch, failure):
    async def run():
        hass = FakeHass()
        entry = FakeConfigEntry()
        entry.state = ConfigEntryState.LOADED
        entry.runtime_data = SimpleNamespace(client=SimpleNamespace(config=object()))
        events = []

        async def unload(entry_id):
            events.append("unload")
            if failure == "unload":
                return False
            entry.state = ConfigEntryState.NOT_LOADED
            return True

        async def setup(entry_id):
            events.append("restore")
            entry.state = ConfigEntryState.LOADED
            return True

        async def probe(config):
            events.append("probe")
            if failure == "timeout":
                raise AmtronConnectionError("timeout")
            if failure == "cancel":
                raise asyncio.CancelledError
            return IDENTITY

        hass.config_entries.async_unload = unload
        hass.config_entries.async_setup = setup
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        flow = Flow(hass)
        flow.reconfigure_entry = entry
        if failure == "wrong_device":
            def reject_identity():
                raise AbortFlow("unique_id_mismatch")
            flow._abort_if_unique_id_mismatch = reject_identity
        if failure in ("cancel", "wrong_device"):
            expected = asyncio.CancelledError if failure == "cancel" else AbortFlow
            with pytest.raises(expected):
                await flow.async_step_network_reconfigure(dict(NETWORK_INPUT))
        else:
            result = await flow.async_step_network_reconfigure(dict(NETWORK_INPUT))
            assert result["errors"] == {"base": "cannot_connect_network"}
        assert entry.state is ConfigEntryState.LOADED
        assert events == (["unload"] if failure == "unload" else ["unload", "probe", "restore"])
        assert flow.updates is None

    asyncio.run(run())


def test_unchanged_network_settings_reuse_the_active_client(monkeypatch):
    async def run():
        hass = FakeHass()
        flow = Flow(hass)
        client = SimpleNamespace(config=NetworkConfig(host=NETWORK_INPUT["host"]))
        flow.reconfigure_entry.state = ConfigEntryState.LOADED
        flow.reconfigure_entry.runtime_data = SimpleNamespace(client=client)
        hass.config_entries.async_unload = AsyncMock()
        read_identity = AsyncMock(return_value=IDENTITY)
        probe = AsyncMock()
        monkeypatch.setattr(config_flow, "async_read_identity", read_identity)
        monkeypatch.setattr(config_flow, "async_test_connection", probe)
        result = await flow.async_step_network_reconfigure(dict(NETWORK_INPUT))
        assert result["reason"] == "reconfigure_successful"
        read_identity.assert_awaited_once_with(client)
        probe.assert_not_awaited()
        hass.config_entries.async_unload.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["rtu_over_tcp", "modbus_tcp"])
@pytest.mark.parametrize("failure", [None, "connect", "identity", "cancel"])
def test_network_probe_never_writes_and_always_closes(monkeypatch, protocol, failure):
    async def run():
        transport = FakeModbusClient(device_bank())
        if failure == "connect":
            transport.connect_result = False
        if failure == "identity":
            transport.bank = {}
        def factory(config):
            return MennekesModbusClient(config, client_factory=lambda _config: transport)
        monkeypatch.setattr(_flow_connect, "MennekesModbusClient", factory)
        if failure == "cancel":
            monkeypatch.setattr(_flow_connect, "async_read_identity", AsyncMock(side_effect=asyncio.CancelledError))
        config = NetworkConfig(host="gateway.local", connection_type=protocol)
        if failure is None:
            assert (await _flow_connect.async_test_connection(config)).serial_number == IDENTITY.serial_number
        else:
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else AmtronBusError):
                await _flow_connect.async_test_connection(config)
        assert transport.writes == []
        assert transport.close_calls == 1

    asyncio.run(run())
