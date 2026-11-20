"""The Modbus connection a direct config entry describes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_STOPBITS,
    CONF_TCP_AUTH_PASSWORD,
    CONF_TCP_AUTHENTICATION,
    CONF_TCP_PORT,
    CONNECTION_SERIAL,
    DEFAULT_TCP_PORT,
)
from .device.connection import ConnectionConfig, NetworkConfig, SerialConfig
from .device.const import (
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
)

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry


def connection_config(entry: MennekesAmtronConfigEntry) -> ConnectionConfig:
    """Load direct transport settings, using serial as the default."""

    data = entry.data
    connection_type = data.get(CONF_CONNECTION_TYPE, CONNECTION_SERIAL)
    if connection_type != CONNECTION_SERIAL:
        return NetworkConfig(
            host=data.get(CONF_HOST, ""),
            tcp_port=data.get(CONF_TCP_PORT, DEFAULT_TCP_PORT),
            connection_type=connection_type,
            device_id=data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            tcp_authentication=data.get(CONF_TCP_AUTHENTICATION, False),
            tcp_auth_password=data.get(CONF_TCP_AUTH_PASSWORD, ""),
        )
    return serial_config(entry)


def serial_config(entry: MennekesAmtronConfigEntry) -> SerialConfig:
    data = entry.data
    return SerialConfig(
        port=str(data[CONF_PORT]),
        baudrate=int(data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)),
        bytesize=int(data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)),
        parity=str(data.get(CONF_PARITY, DEFAULT_PARITY)),
        stopbits=int(data.get(CONF_STOPBITS, DEFAULT_STOPBITS)),
        device_id=int(data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)),
    )
