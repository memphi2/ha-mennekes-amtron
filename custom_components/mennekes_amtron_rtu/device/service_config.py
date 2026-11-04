"""Strict service configuration; credentials never appear in repr or logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import SerialConfig
from .const import SUPPORTED_BAUDRATES, frame_parts


@dataclass(frozen=True, slots=True)
class ServerConfig:
    serial: SerialConfig
    token: str = field(repr=False)
    read_only: bool = True
    current_cap: float = 6.0
    scan_interval: float = 5.0
    measurement_interval: float = 30.0
    listen: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ServerConfig:
        allowed = {
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
        }
        if values.keys() - allowed:
            raise ValueError("unknown service configuration option")
        token = values.get("token")
        if (
            not isinstance(token, str)
            or len(token) < 32
            or not token.isascii()
            or any(char.isspace() for char in token)
        ):
            raise ValueError(
                "token must contain at least 32 ASCII characters without whitespace"
            )
        serial_port = values.get("serial_port")
        if (
            not isinstance(serial_port, str)
            or not serial_port.startswith("/dev/")
            or "\x00" in serial_port
        ):
            raise ValueError("serial_port must be a local /dev/ device")
        baudrate = integer(values.get("baudrate", 57600), 9600, 57600)
        if baudrate not in SUPPORTED_BAUDRATES:
            raise ValueError("unsupported baudrate")
        frame = values.get("frame", "8n2")
        if frame not in ("8n2", "8e1", "8o1"):
            raise ValueError("unsupported serial frame")
        bits, parity, stops = frame_parts(frame)
        read_only = values.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ValueError("read_only must be boolean")
        listen = values.get("listen", "127.0.0.1")
        if not isinstance(listen, str) or not listen or len(listen) > 253:
            raise ValueError("invalid listen address")
        return cls(
            SerialConfig(
                serial_port,
                baudrate,
                bits,
                parity,
                stops,
                integer(values.get("device_id", 50), 10, 50),
            ),
            token,
            read_only,
            number(values.get("current_cap", 6), 6, 32),
            number(values.get("scan_interval", 5), 2, 300),
            number(values.get("measurement_interval", 30), 2, 300),
            listen,
            integer(values.get("port", 8765), 1, 65535),
        )


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
