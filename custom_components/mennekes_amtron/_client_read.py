"""Block reads with a single-register fallback.

The caller is expected to hold the bus lock: RS-485 is a single-master bus,
so these helpers never serialize anything themselves.
"""

from __future__ import annotations

import logging
from typing import Any

from pymodbus.exceptions import ConnectionException, ModbusException
from pymodbus.pdu import ExceptionResponse

from .client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
    protocol_error_for,
)
from .decode import RegisterValue, decode_register
from .register_blocks import RegisterBlock

_LOGGER = logging.getLogger(__name__)


async def read_words(
    client: Any,
    *,
    address: int,
    count: int,
    device_id: int,
) -> list[int]:
    """Read ``count`` holding registers and return their raw words."""

    try:
        response = await client.read_holding_registers(
            address, count=count, device_id=device_id
        )
    except ConnectionException as err:
        raise AmtronConnectionError(str(err) or "serial connection failed") from err
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
) -> dict[str, RegisterValue]:
    """Read one block, falling back to single registers when rejected.

    A device that advertises a layout version but still rejects part of a
    block would otherwise lose every register in that block.
    """

    try:
        words = await read_words(
            client, address=block.address, count=block.count, device_id=device_id
        )
    except AmtronIllegalAddressError:
        _LOGGER.debug(
            "Block %s rejected as a range read, falling back to single registers",
            block.name,
        )
        return await _read_block_registerwise(
            client, block=block, device_id=device_id
        )

    decoded: dict[str, RegisterValue] = {}
    for spec in block.specs:
        offset = spec.address - block.address
        decoded[spec.key] = decode_register(spec, words[offset : offset + spec.count])
    return decoded


async def _read_block_registerwise(
    client: Any,
    *,
    block: RegisterBlock,
    device_id: int,
) -> dict[str, RegisterValue]:
    decoded: dict[str, RegisterValue] = {}
    for spec in block.specs:
        try:
            words = await read_words(
                client,
                address=spec.address,
                count=spec.count,
                device_id=device_id,
            )
        except AmtronIllegalAddressError:
            decoded[spec.key] = None
            continue
        decoded[spec.key] = decode_register(spec, words)
    return decoded
