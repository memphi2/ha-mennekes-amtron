"""Validate transport boundaries and exercise the service without USB settings."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from pymodbus import FramerType

from custom_components.mennekes_amtron_rtu.device import client as client_module
from custom_components.mennekes_amtron_rtu.device import server
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.connection import (
    NetworkConfig,
    SerialConfig,
    validate_network_host,
    validate_tcp_port,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from tests.fakes import FakeModbusClient, device_bank
from tests.test_modbus_service import TOKEN, current_writes

PROTOCOLS = ["rtu_over_tcp", "modbus_tcp"]


@pytest.mark.parametrize(
    ("host", "canonical"),
    [
        ("192.0.2.10", "192.0.2.10"),
        ("2001:0db8::1", "2001:db8::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("Gateway.Local.", "gateway.local"),
        ("gateway-1", "gateway-1"),
        ("gäteway.local", "xn--gteway-bua.local"),
    ],
)
def test_hosts_normalize_without_mixing_in_urls_or_ports(host, canonical):
    assert validate_network_host(host) == canonical
    config = NetworkConfig(host, tcp_port=1502)
    assert config.host == canonical
    expected_host = f"[{canonical}]" if ":" in canonical else canonical
    assert config.port == f"rtu_over_tcp://{expected_host}:1502"


@pytest.mark.parametrize(
    "host",
    [
        None, True, 123, "", " ", "gateway.local\n", "gateway.local\x00",
        "http://gateway", "socket://gateway:502", "user:secret@gateway",
        "gateway/path", "gateway\\path", "gateway?secret", "gateway#fragment",
        "gateway:502", "[192.0.2.10]", "[bad:ipv6]", "2001:db8::invalid",
        "fe80::1%eth0", "999.1.1.1", "gateway..local", "gateway..", ".",
        "-gateway", "gateway-", "a" * 64 + ".local", "a" * 254,
    ],
)
def test_invalid_hosts_fail_without_echoing_supplied_value(host):
    with pytest.raises(ValueError, match="host") as error:
        NetworkConfig(host)
    if isinstance(host, str) and len(host) > 1:
        assert host not in str(error.value)


@pytest.mark.parametrize("port", [0, 65536, -1, True, False, "502", 502.0, None])
def test_tcp_port_requires_exact_integer(port):
    with pytest.raises(ValueError, match="tcp_port"):
        validate_tcp_port(port)


@pytest.mark.parametrize("port", [1, 502, 65535])
def test_tcp_port_boundaries(port):
    assert validate_tcp_port(port) == port


@pytest.mark.parametrize(
    "changes",
    [
        {"connection_type": "serial"}, {"connection_type": None},
        {"connection_type": []}, {"device_id": 9}, {"device_id": 51},
        {"device_id": True}, {"timeout": 0}, {"timeout": -1},
        {"timeout": float("nan")}, {"timeout": float("inf")},
        {"timeout": True}, {"retries": -1}, {"retries": True},
    ],
)
def test_network_connection_rejects_invalid_bus_parameters(changes):
    with pytest.raises(ValueError, match=next(iter(changes))):
        NetworkConfig("gateway.local", **changes)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_network_service_needs_no_usb_and_separates_tcp_and_http_ports(protocol):
    config = ServerConfig.from_dict({
        "token": TOKEN,
        "connection_type": protocol,
        "host": "Gateway.Local",
        "tcp_port": 1502,
        "port": 8766,
        "device_id": 10,
        # Inactive settings may remain after switching the App to LAN.
        "serial_port": None,
        "baudrate": "unused",
        "frame": None,
    })
    assert config.connection == NetworkConfig(
        "gateway.local", tcp_port=1502, connection_type=protocol, device_id=10
    )
    assert config.port == 8766
    default = ServerConfig.from_dict({
        "token": TOKEN, "connection_type": protocol, "host": "192.0.2.10"
    })
    assert default.connection.tcp_port == 502
    assert default.connection.device_id == 50
    assert default.port == 8765


def test_serial_default_keeps_old_constructor_and_ignores_inactive_network_fields():
    config = ServerConfig.from_dict({
        "token": TOKEN, "serial_port": "/dev/fake", "host": None, "tcp_port": None
    })
    assert config.connection == SerialConfig("/dev/fake")
    assert config.connection.connection_type == "serial"
    assert client_module.SerialConfig is SerialConfig
    assert ServerConfig(SerialConfig("/dev/fake"), TOKEN) == config


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"connection_type": "tcp"}, "connection_type"),
        ({"connection_type": []}, "connection_type"),
        ({"host": None}, "host"),
        ({"host": "http://secret:password@gateway"}, "host"),
        ({"tcp_port": "502"}, "tcp_port"),
        ({"tcp_port": 0}, "tcp_port"),
        ({"device_id": 51}, "device_id"),
        ({"port": True}, "port"),
    ],
)
def test_network_service_rejects_bad_settings_without_secrets(changes, field):
    with pytest.raises(ValueError, match=field) as error:
        ServerConfig.from_dict({
            "token": TOKEN, "connection_type": "rtu_over_tcp",
            "host": "gateway.local", **changes,
        })
    assert "secret" not in str(error.value)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    ("protocol", "framer"),
    [("rtu_over_tcp", FramerType.RTU), ("modbus_tcp", FramerType.SOCKET)],
)
def test_network_factory_keeps_tracing_but_disables_library_write_retry_and_reconnect(
    monkeypatch, protocol, framer
):
    created = []
    transport = FakeModbusClient(device_bank())

    def create(host, **kwargs):
        created.append((host, kwargs))
        return transport

    monkeypatch.setattr(client_module, "AsyncModbusTcpClient", create)

    async def run():
        client = MennekesModbusClient(NetworkConfig(
            "gateway.local", tcp_port=1502, connection_type=protocol, timeout=2, retries=4
        ))
        try:
            await client.async_connect()
            host, options = created[0]
            assert host == "gateway.local"
            assert options == {
                "port": 1502, "framer": framer, "timeout": 2, "retries": 0,
                "reconnect_delay": 0, "trace_packet": client.write_trace.packet,
            }
            assert client.config.retries == 4  # Our explicit read retry budget remains.
        finally:
            await client.async_close()
        assert not transport.connected

    asyncio.run(run())


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_network_service_controls_restore_pause_and_log_the_actual_transport(
    tmp_path, caplog, protocol
):
    caplog.set_level(logging.INFO)
    connection = NetworkConfig("2001:db8::1", tcp_port=1502, connection_type=protocol)
    config = ServerConfig(connection, TOKEN, read_only=False, current_cap=16)
    transport = FakeModbusClient(device_bank(serial="", charging_current=8))

    async def create_service():
        client = MennekesModbusClient(connection, client_factory=lambda _: transport)
        service = ModbusService(config, tmp_path / "intent.json", client=client)
        service.start()
        async with asyncio.timeout(3):
            await service._heartbeat_started.wait()
        assert service._store.identity == f"{protocol}://[2001:db8::1]:1502:50"
        return service

    async def run():
        service = await create_service()
        try:
            await service.command({"command": "pause"})
            assert service.control.pause_requested is True
            accepted = current_writes(transport)
            assert accepted == [1]
        finally:
            await service.stop()
        restored = await create_service()
        try:
            assert restored.control.pause_requested is True
            assert restored.control.charging_setpoint == 8
            assert current_writes(transport) == accepted
        finally:
            await restored.stop()
        assert not transport.connected

    asyncio.run(run())
    label = "RTU-over-TCP" if protocol == "rtu_over_tcp" else "Modbus TCP"
    assert f"Opening {label} connection" in caplog.text
    assert f"{label} connection established; wallbox initialized" in caplog.text
    assert f"{label} connection closed" in caplog.text
    assert "Heartbeat started" in caplog.text
    assert "Heartbeat stopped" in caplog.text
    assert "serial connection" not in caplog.text.lower()
    assert "2001:db8::1" not in caplog.text


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_network_cli_accepts_app_options_and_logs_only_relevant_fields(
    tmp_path, monkeypatch, caplog, protocol
):
    caplog.set_level(logging.INFO)
    path = tmp_path / "options.json"
    path.write_text(json.dumps({
        "token": TOKEN, "connection_type": protocol,
        "host": "private-gateway.local", "tcp_port": 1502,
    }))
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["amtron-modbus-service", "--config", str(path)])
    seen = []

    async def run(config, *_args):
        seen.append(config)

    monkeypatch.setattr(server, "run", run)
    server.main()
    assert seen[0].connection.connection_type == protocol
    assert f"transport={protocol}, Modbus address=50, TCP port=1502" in caplog.text
    assert "baudrate" not in caplog.text
    assert "private-gateway" not in caplog.text
    assert TOKEN not in caplog.text
