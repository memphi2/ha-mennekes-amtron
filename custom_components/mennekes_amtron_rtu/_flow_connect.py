"""The config flow's real connection test.

The flow opens the bus and reads the layout version and serial number before
an entry is created, so a wrong port, baud rate or device id fails during
setup instead of producing an entry with no entities.
"""

from __future__ import annotations

from .client import MennekesModbusClient, SerialConfig
from .client_errors import AmtronBusError
from .data import DeviceIdentity
from .identity import async_read_identity


async def async_test_connection(config: SerialConfig) -> DeviceIdentity:
    """Connect once, read the device identity and close the port again."""

    client = MennekesModbusClient(config)
    try:
        await client.async_connect()
        return await async_read_identity(client)
    finally:
        await client.async_close()


__all__ = ["AmtronBusError", "async_test_connection"]
