from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron._client_read import read_block, read_words
from custom_components.mennekes_amtron.client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
)
from custom_components.mennekes_amtron.register_blocks import REGISTER_BLOCKS
from tests.fakes import (
    FakeModbusClient,
    device_bank,
    raise_connection_error,
    raise_modbus_error,
)

CONFIGURATION = next(
    block for block in REGISTER_BLOCKS if block.name == "configuration"
)
STATISTICS = next(block for block in REGISTER_BLOCKS if block.name == "statistics")


def test_words_are_returned_in_order() -> None:
    transport = FakeModbusClient({0x0100: 5, 0x0101: 1})
    words = asyncio.run(read_words(transport, address=0x0100, count=2, device_id=50))
    assert words == [5, 1]


def test_a_dead_link_becomes_a_connection_error() -> None:
    transport = FakeModbusClient()
    transport.read_exceptions[0x0100] = raise_connection_error()
    with pytest.raises(AmtronConnectionError):
        asyncio.run(read_words(transport, address=0x0100, count=1, device_id=50))


def test_a_protocol_error_becomes_a_protocol_error() -> None:
    transport = FakeModbusClient()
    transport.read_exceptions[0x0100] = raise_modbus_error()
    with pytest.raises(AmtronProtocolError):
        asyncio.run(read_words(transport, address=0x0100, count=1, device_id=50))


def test_an_error_response_without_a_code_is_still_an_error() -> None:
    transport = FakeModbusClient()
    transport.read_exceptions[0x0100] = "error"
    with pytest.raises(AmtronProtocolError, match="0x0100"):
        asyncio.run(read_words(transport, address=0x0100, count=1, device_id=50))


def test_an_illegal_address_is_reported_as_such() -> None:
    transport = FakeModbusClient()
    transport.read_exceptions[0x1000] = 0x02
    with pytest.raises(AmtronIllegalAddressError):
        asyncio.run(read_words(transport, address=0x1000, count=4, device_id=50))


def test_a_short_frame_is_rejected() -> None:
    class ShortClient(FakeModbusClient):
        async def read_holding_registers(
            self, address: int, *, count: int = 1, device_id: int = 1
        ) -> object:
            return await super().read_holding_registers(
                address, count=count - 1, device_id=device_id
            )

    with pytest.raises(AmtronProtocolError, match="of 2 registers"):
        asyncio.run(
            read_words(ShortClient({0x0100: 1}), address=0x0100, count=2, device_id=50)
        )


def test_a_block_read_decodes_every_register() -> None:
    transport = FakeModbusClient(device_bank())
    decoded = asyncio.run(read_block(transport, block=STATISTICS, device_id=50))
    assert decoded["energy_total"] == pytest.approx(1234.5)
    assert decoded["sessions_total"] == 42


def test_a_rejected_block_falls_back_to_single_registers() -> None:
    transport = FakeModbusClient(device_bank())
    transport.read_exceptions[CONFIGURATION.address] = 0x02

    decoded = asyncio.run(read_block(transport, block=CONFIGURATION, device_id=50))

    assert decoded["cable_lock_setting"] is None
    assert decoded["ems_fallback_current"] == 6
    assert decoded["phase_switching_pause"] == 120
    # One rejected range read, then one read per register of the block.
    assert len(transport.reads) == 1 + len(CONFIGURATION.keys)


def test_a_register_that_stays_rejected_decodes_to_none() -> None:
    transport = FakeModbusClient(device_bank())
    transport.read_exceptions[STATISTICS.address] = 0x02

    decoded = asyncio.run(read_block(transport, block=STATISTICS, device_id=50))

    assert decoded == {"energy_total": None, "sessions_total": 42}
