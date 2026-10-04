"""Bus-level error types and Modbus exception-code mapping.

These are transport errors, not Home Assistant errors: the client raises
them, and ``control.py`` plus the coordinator translate them into user-facing
Home Assistant exceptions.
"""

from __future__ import annotations

from typing import Final

ILLEGAL_FUNCTION: Final = 0x01
ILLEGAL_DATA_ADDRESS: Final = 0x02
ILLEGAL_DATA_VALUE: Final = 0x03
SERVER_DEVICE_FAILURE: Final = 0x04


class AmtronBusError(Exception):
    """Base error for every failed bus transaction."""


class AmtronConnectionError(AmtronBusError):
    """The serial port or the device did not answer at all."""


class AmtronProtocolError(AmtronBusError):
    """The device answered, but not with usable data."""


class AmtronIllegalAddressError(AmtronProtocolError):
    """The device rejected the address as unknown.

    Older firmware answers this way for registers that a newer layout added,
    which is why the read path can fall back to single-register reads.
    """


class AmtronRateLimitedError(AmtronBusError):
    """A write came sooner than the vendor's minimum write interval allows."""

    def __init__(self, key: str, retry_after: float) -> None:
        super().__init__(
            f"{key} may be written again in {retry_after:.1f} s"
        )
        self.key = key
        self.retry_after = retry_after


class AmtronWriteRejectedError(AmtronProtocolError):
    """The device refused a write.

    The usual cause is a wallbox that is not configured as a Modbus satellite
    (DIP bank S1, DIP 4 and DIP 5 on, followed by a restart).
    """


def protocol_error_for(exception_code: int, address: int) -> AmtronProtocolError:
    """Return the matching error for a Modbus exception response."""

    location = f"0x{address:04X}"
    if exception_code == ILLEGAL_DATA_ADDRESS:
        return AmtronIllegalAddressError(
            f"device reports register {location} as unknown"
        )
    if exception_code in (ILLEGAL_FUNCTION, ILLEGAL_DATA_VALUE):
        return AmtronWriteRejectedError(
            f"device rejected the access to {location} "
            f"(modbus exception {exception_code})"
        )
    return AmtronProtocolError(
        f"device returned modbus exception {exception_code} for {location}"
    )
