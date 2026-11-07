"""Connection settings shared by direct HA and the independent bus service."""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .const import (
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PARITY,
    DEFAULT_RETRIES,
    DEFAULT_STOPBITS,
    DEFAULT_TIMEOUT,
)


@dataclass(frozen=True, slots=True)
class SerialConfig:
    """Serial bus parameters; defaults match the wallbox factory settings."""

    port: str
    baudrate: int = DEFAULT_BAUDRATE
    bytesize: int = DEFAULT_BYTESIZE
    parity: str = DEFAULT_PARITY
    stopbits: int = DEFAULT_STOPBITS
    device_id: int = DEFAULT_DEVICE_ID
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES

    @property
    def connection_type(self) -> Literal["serial"]:
        return "serial"


def validate_network_host(value: Any) -> str:
    """Accept an IP address or DNS hostname, never a URL or credentials."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 253
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or any(char in value for char in "/\\@?#%")
    ):
        raise ValueError("host must be an IP address or hostname without a URL or credentials")
    candidate = value
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
        try:
            return str(ipaddress.IPv6Address(candidate))
        except ValueError:
            raise ValueError("host contains an invalid IPv6 address") from None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    if ":" in candidate or re.fullmatch(r"\d+(?:\.\d+){3}", candidate):
        raise ValueError("host contains an invalid IP address")
    try:
        hostname = candidate.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("host contains an invalid hostname") from None
    if len(hostname) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError("host contains an invalid hostname")
    return hostname


def validate_tcp_port(value: Any) -> int:
    """Keep the gateway port separate from the service's HTTP API port."""
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("tcp_port must be an integer from 1 to 65535")
    return value


def validate_tcp_auth_password(value: Any) -> str:
    """Match the verified wired gateway's nine-character web-password field."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 9
        or any(not 0x21 <= ord(char) <= 0x7E for char in value)
    ):
        raise ValueError("adapter password must contain 1 to 9 printable ASCII characters without spaces")
    return value


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """A TCP connection to a transparent adapter or a Modbus TCP gateway."""

    host: str
    tcp_port: int = 502
    connection_type: Literal["rtu_over_tcp", "modbus_tcp"] = "rtu_over_tcp"
    device_id: int = DEFAULT_DEVICE_ID
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    tcp_authentication: bool = False
    tcp_auth_password: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", validate_network_host(self.host))
        validate_tcp_port(self.tcp_port)
        if self.connection_type not in ("rtu_over_tcp", "modbus_tcp"):
            raise ValueError("connection_type must be rtu_over_tcp or modbus_tcp")
        if type(self.device_id) is not int or not 10 <= self.device_id <= 50:
            raise ValueError("device_id must be an integer from 10 to 50")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        if type(self.retries) is not int or self.retries < 0:
            raise ValueError("retries must be a non-negative integer")
        if type(self.tcp_authentication) is not bool:
            raise ValueError("tcp_authentication must be boolean")
        if not isinstance(self.tcp_auth_password, str):
            raise ValueError("adapter password must be a string")
        if self.tcp_authentication:
            validate_tcp_auth_password(self.tcp_auth_password)
        elif self.tcp_auth_password:
            raise ValueError("adapter password requires TCP authentication")

    @property
    def port(self) -> str:
        """A canonical endpoint for diagnostics and fallback device identity."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.connection_type}://{host}:{self.tcp_port}"


type ConnectionConfig = SerialConfig | NetworkConfig
