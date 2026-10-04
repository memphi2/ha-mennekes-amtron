from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.client_errors import AmtronBusError
from custom_components.mennekes_amtron.identity import async_read_identity
from tests.fakes import FakeModbusClient, device_bank

CONFIG = SerialConfig(port="/dev/fake", device_id=50)


async def _identity(transport: FakeModbusClient) -> object:
    client = MennekesModbusClient(CONFIG, client_factory=lambda _config: transport)
    await client.async_connect()
    return await async_read_identity(client)


def test_a_validated_device_reports_everything() -> None:
    identity = asyncio.run(_identity(FakeModbusClient(device_bank())))
    assert identity.layout_version == R.LAYOUT_V01_03
    assert identity.serial_number == "ABC123456789"
    assert identity.article_number == "1313201205"
    assert identity.firmware_version == "2023.21.11024"
    assert identity.phase_options_hw == 2
    assert identity.max_evse_current == 16.0


def test_an_older_layout_skips_the_registers_it_lacks() -> None:
    bank = device_bank(layout=R.LAYOUT_V01_00)
    identity = asyncio.run(_identity(FakeModbusClient(bank)))
    assert identity.layout_version == R.LAYOUT_V01_00
    assert identity.serial_number is None
    assert identity.article_number is None
    assert identity.phase_options_hw is None
    assert identity.firmware_version == "2023.21.11024"


def test_a_register_that_fails_is_simply_absent() -> None:
    transport = FakeModbusClient(device_bank())
    transport.read_exceptions[R.SERIAL_NUMBER.address] = 0x02
    identity = asyncio.run(_identity(transport))
    assert identity.serial_number is None
    assert identity.article_number == "1313201205"


def test_a_device_without_a_layout_version_is_refused() -> None:
    transport = FakeModbusClient({R.MODBUS_LAYOUT_VERSION.address: 0})
    with pytest.raises(AmtronBusError, match="layout version"):
        asyncio.run(_identity(transport))


def test_an_unreadable_maximum_current_is_absent() -> None:
    bank = device_bank()
    bank[R.MAX_EVSE_CURRENT.address] = 0x7FC0
    bank[R.MAX_EVSE_CURRENT.address + 1] = 0x0000
    identity = asyncio.run(_identity(FakeModbusClient(bank)))
    assert identity.max_evse_current is None
