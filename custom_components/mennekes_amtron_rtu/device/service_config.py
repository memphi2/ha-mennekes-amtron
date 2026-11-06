"""Strict service configuration; credentials never appear in repr or logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .connection import ConnectionConfig, NetworkConfig, SerialConfig
from .const import (
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
    SUPPORTED_BAUDRATES,
    frame_parts,
)

# The app exposes only three readable choices. Standalone JSON configurations
# keep their canonical transport names; normalize both forms at this boundary.
_CONNECTION_TYPES = {
    "USB / RS-485": "serial",
    "LAN: Modbus RTU (TCP)": "rtu_over_tcp",
    "LAN: Modbus TCP": "modbus_tcp",
    "serial": "serial",
    "rtu_over_tcp": "rtu_over_tcp",
    "modbus_tcp": "modbus_tcp",
}


def validate_token(value: Any) -> str:
    """Validate credentials without including them in error messages."""
    if (
        not isinstance(value, str)
        or len(value) < 32
        or not value.isascii()
        or any(char.isspace() for char in value)
    ):
        raise ValueError(
            "token must contain at least 32 ASCII characters without whitespace"
        )
    return value


@dataclass(frozen=True, slots=True)
class ServerConfig:
    connection: ConnectionConfig
    token: str = field(repr=False)
    read_only: bool = True
    current_cap: float = 6.0
    scan_interval: float = 5.0
    measurement_interval: float = 30.0
    listen: str = "127.0.0.1"
    port: int = 8765
    state_change_interval_seconds: float = DEFAULT_STATE_CHANGE_INTERVAL_SECONDS

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ServerConfig:
        allowed = {
            "connection_type",
            "host",
            "tcp_port",
            "serial_port",
            "baudrate",
            "device_id",
            "frame",
            "token",
            "read_only",
            "current_cap",
            "scan_interval",
            "measurement_interval",
            "listen",
            "port",
            "state_change_interval_seconds",
        }
        if values.keys() - allowed:
            raise ValueError("unknown service configuration option")
        token = validate_token(values.get("token"))
        connection = _connection_options(values)
        read_only = values.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be boolean")
        listen = values.get("listen", "127.0.0.1")
        if not isinstance(listen, str) or not listen or len(listen) > 253:
            raise ValueError("invalid listen address")
        return cls(
            connection,
            token,
            read_only,
            _number_option(values.get("current_cap", 6), "current_cap", 6, 32),
            _number_option(values.get("scan_interval", 5), "scan_interval", 2, 300),
            _number_option(
                values.get("measurement_interval", 30), "measurement_interval", 2, 300
            ),
            listen,
            _integer_option(values.get("port", 8765), "port", 1, 65535),
            _integer_option(
                values.get("state_change_interval_seconds", DEFAULT_STATE_CHANGE_INTERVAL_SECONDS),
                "state_change_interval_seconds", 0, MAX_STATE_CHANGE_INTERVAL_SECONDS,
            ),
        )


def _connection_options(values: dict[str, Any]) -> ConnectionConfig:
    """Validate only the fields used by the selected physical transport."""
    selection = values.get("connection_type", "serial")
    if not isinstance(selection, str) or selection not in _CONNECTION_TYPES:
        raise ValueError("connection_type must select a supported USB or LAN connection")
    connection_type = _CONNECTION_TYPES[selection]
    device_id = _integer_option(values.get("device_id", 50), "device_id", 10, 50)
    if connection_type != "serial":
        return NetworkConfig(
            host=values.get("host", ""),
            tcp_port=values.get("tcp_port", 502),
            connection_type=cast(Literal["rtu_over_tcp", "modbus_tcp"], connection_type),
            device_id=device_id,
        )
    serial_port = values.get("serial_port")
    if (
        not isinstance(serial_port, str)
        or not serial_port.startswith("/dev/")
        or "\x00" in serial_port
    ):
        raise ValueError("serial_port must be a local /dev/ device")
    baudrate_value = values.get("baudrate", 57600)
    # Supervisor list(...) choices are strings, including digit-only options.
    if isinstance(baudrate_value, str) and baudrate_value in {
        str(rate) for rate in SUPPORTED_BAUDRATES
    }:
        baudrate_value = int(baudrate_value)
    baudrate = _integer_option(baudrate_value, "baudrate", 9600, 57600)
    if baudrate not in SUPPORTED_BAUDRATES:
        raise ValueError("unsupported baudrate")
    frame = values.get("frame", "8n2")
    if frame not in ("8n2", "8e1", "8o1"):
        raise ValueError("unsupported serial frame")
    bits, parity, stops = frame_parts(frame)
    return SerialConfig(serial_port, baudrate, bits, parity, stops, device_id)


def number(value: Any, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= value <= maximum
    ):
        raise ValueError("numeric value is outside the allowed range")
    return float(value)


def integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("integer value is outside the allowed range")
    return value


def _integer_option(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        return integer(value, minimum, maximum)
    except ValueError as error:
        raise ValueError(f"{name}: {error}") from None


def _number_option(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        return number(value, minimum, maximum)
    except ValueError as error:
        raise ValueError(f"{name}: {error}") from None
