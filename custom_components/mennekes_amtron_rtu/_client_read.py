"""Block reads with a single-register fallback.

The caller serializes each transaction on the single-master RS-485 bus.
Block reads can accept a callback that releases the bus between fallback
transactions, allowing a priority heartbeat to pass.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pymodbus.exceptions import ConnectionException, ModbusException, ModbusIOException
from pymodbus.pdu import ExceptionResponse

from .client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
    protocol_error_for,
)
from .decode import RegisterValue, decode_block, decode_register
from .register_blocks import RegisterBlock

_LOGGER = logging.getLogger(__name__)


async def read_words(
    client: Any,
    *,
    address: int,
    count: int,
    device_id: int,
    retries: int = 0,
) -> list[int]:
    """Read words, retrying unanswered reads without retrying any writes."""

    remaining = retries
    while True:
        try:
            response = await client.read_holding_registers(
                address, count=count, device_id=device_id
            )
            break
        except ConnectionException as err:
            raise AmtronConnectionError(str(err) or "serial connection failed") from err
        except ModbusIOException as err:
            # pymodbus 3.13 wraps cancellation in ModbusIOException. Preserve
            # task cancellation instead of treating it as another read timeout.
            if isinstance(err.__cause__, asyncio.CancelledError):
                raise err.__cause__ from None
            if remaining <= 0:
                raise AmtronProtocolError(str(err) or "modbus read failed") from err
            remaining -= 1
        except ModbusException as err:
            raise AmtronProtocolError(str(err) or "modbus transaction failed") from err

    if isinstance(response, ExceptionResponse):
        raise protocol_error_for(response.exception_code, address)
    if response is None or response.isError():
        raise AmtronProtocolError(f"read of 0x{address:04X} failed")

    words = [int(word) for word in getattr(response, "registers", [])]
    if len(words) != count:
        raise AmtronProtocolError(
            f"read of 0x{address:04X} returned {len(words)} of {count} registers"
        )
    return words


async def read_block(
    client: Any,
    *,
    block: RegisterBlock,
    device_id: int,
    read: Callable[[int, int], Awaitable[list[int]]] | None = None,
) -> dict[str, RegisterValue]:
    """Read one block, falling back to single registers when rejected.

    A device that advertises a layout version but still rejects part of a
    block would otherwise lose every register in that block.
    """

    read_range = read or (
        lambda address, count: read_words(
            client, address=address, count=count, device_id=device_id
        )
    )
    try:
        words = await read_range(block.address, block.count)
    except AmtronIllegalAddressError:
        _LOGGER.debug(
            "Block %s rejected as a range read, falling back to single registers",
            block.name,
        )
        return await _read_block_registerwise(block=block, read=read_range)

    return decode_block(block, words)


async def _read_block_registerwise(
    *,
    block: RegisterBlock,
    read: Callable[[int, int], Awaitable[list[int]]],
) -> dict[str, RegisterValue]:
    decoded: dict[str, RegisterValue] = {}
    for spec in block.specs:
        try:
            words = await read(spec.address, spec.count)
        except AmtronIllegalAddressError:
            decoded[spec.key] = None
            continue
        decoded[spec.key] = decode_register(spec, words)
    return decoded
