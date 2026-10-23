"""Register writes, write rate limiting and write-value validation.

The caller is expected to hold the bus lock. Rate limiting lives here rather
than in the control layer so every write path on the bus is covered, not just
the ones a future caller remembers to throttle.
"""

from __future__ import annotations

import logging
from typing import Any

from pymodbus.exceptions import ConnectionException, ModbusException
from pymodbus.pdu import ExceptionResponse

from .client_errors import (
    AmtronConnectionError,
    AmtronProtocolError,
    AmtronWriteRejectedError,
    protocol_error_for,
)
from .decode import encode_register
from .registers import RegisterSpec

_LOGGER = logging.getLogger(__name__)


class WriteRateLimiter:
    """Smallest allowed distance between two writes of the same register."""

    def __init__(self) -> None:
        self._last_write: dict[str, float] = {}

    def remaining(self, key: str, *, now: float, min_interval: float) -> float:
        """Return the seconds still to wait before writing ``key`` again."""

        if min_interval <= 0:
            return 0.0
        last = self._last_write.get(key)
        if last is None:
            return 0.0
        return max(0.0, min_interval - (now - last))

    def record(self, key: str, *, now: float) -> None:
        """Remember that ``key`` was written at ``now``."""

        self._last_write[key] = now

    def forget(self, key: str) -> None:
        """Drop the recorded time of a register, used when a write failed."""

        self._last_write.pop(key, None)


def validate_write(spec: RegisterSpec, value: float) -> None:
    """Reject a value the specification does not allow for a register."""

    if not spec.writable:
        raise AmtronWriteRejectedError(f"{spec.key} is not writable")
    if spec.enum_map is not None and int(value) not in spec.enum_map:
        allowed = ", ".join(str(key) for key in sorted(spec.enum_map))
        raise AmtronWriteRejectedError(
            f"{spec.key} accepts only {allowed}, got {value}"
        )


async def write_register(
    client: Any,
    *,
    spec: RegisterSpec,
    value: float,
    device_id: int,
) -> None:
    """Write one register range.

    Single-register values go out as function 0x06, and multi-register values
    such as float32 as function 0x10, which the specification requires.
    """

    validate_write(spec, value)
    words = encode_register(spec, value)
    try:
        if len(words) == 1:
            response = await client.write_register(
                spec.address, words[0], device_id=device_id
            )
        else:
            response = await client.write_registers(
                spec.address, words, device_id=device_id
            )
    except ConnectionException as err:
        raise AmtronConnectionError(str(err) or "serial connection failed") from err
    except ModbusException as err:
        raise AmtronProtocolError(str(err) or "modbus transaction failed") from err

    if isinstance(response, ExceptionResponse):
        raise protocol_error_for(response.exception_code, spec.address)
    if response is None or response.isError():
        raise AmtronWriteRejectedError(
            f"device rejected the write to 0x{spec.address:04X}"
        )
    _LOGGER.debug("Wrote %s = %s to 0x%04X", spec.key, value, spec.address)
