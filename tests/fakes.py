"""Shared fakes for the MENNEKES AMTRON test suite.

``FakeModbusClient`` replaces the pymodbus transport, not the integration's
own client: the real lock, the real error mapping and the real decoding are
exercised against register words that look exactly like the device's.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pymodbus.exceptions import ConnectionException, ModbusException
from pymodbus.pdu import ExceptionResponse

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.decode import encode_register


@dataclass
class FakeResponse:
    """A successful pymodbus read or write response."""

    registers: list[int] = field(default_factory=list)

    def isError(self) -> bool:  # noqa: N802 - pymodbus API name
        """Return false: this response carries data."""

        return False


class FakeModbusClient:
    """An in-memory stand-in for ``AsyncModbusSerialClient``."""

    def __init__(self, bank: dict[int, int] | None = None) -> None:
        self.bank: dict[int, int] = dict(bank or {})
        self.connected = False
        self.connect_result = True
        self.connect_error: Exception | None = None
        self.read_exceptions: dict[int, Any] = {}
        self.write_exceptions: dict[int, Any] = {}
        self.read_delay = 0.0
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, list[int]]] = []
        self.close_calls = 0
        self.concurrent_reads = 0
        self.max_concurrent = 0

    async def connect(self) -> bool:
        """Open the fake transport."""

        if self.connect_error is not None:
            raise self.connect_error
        self.connected = self.connect_result
        return self.connect_result

    def close(self) -> None:
        """Close the fake transport."""

        self.close_calls += 1
        self.connected = False

    async def read_holding_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> Any:
        """Return the words of a register range."""

        self.reads.append((address, count))
        self.concurrent_reads += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent_reads)
        try:
            if self.read_delay:
                await asyncio.sleep(self.read_delay)
            failure = self.read_exceptions.get(address)
            if failure is not None:
                return _raise_or_return(failure, address, 0x03)
            return FakeResponse(
                [self.bank.get(address + offset, 0) for offset in range(count)]
            )
        finally:
            self.concurrent_reads -= 1

    async def write_register(
        self, address: int, value: int, *, device_id: int = 1
    ) -> Any:
        """Write a single register."""

        return self._write(address, [value], 0x06)

    async def write_registers(
        self, address: int, values: list[int], *, device_id: int = 1
    ) -> Any:
        """Write several registers."""

        return self._write(address, list(values), 0x10)

    def _write(self, address: int, values: list[int], function: int) -> Any:
        failure = self.write_exceptions.get(address)
        if failure is not None:
            return _raise_or_return(failure, address, function)
        self.writes.append((address, values))
        for offset, value in enumerate(values):
            self.bank[address + offset] = value
        return FakeResponse(values)


def _raise_or_return(failure: Any, address: int, function: int) -> Any:
    if isinstance(failure, BaseException):
        raise failure
    if failure == "error":
        return _ErrorResponse()
    return ExceptionResponse(function, int(failure))


class _ErrorResponse:
    """A response whose ``isError`` is true without an exception code."""

    registers: list[int] = []

    def isError(self) -> bool:  # noqa: N802 - pymodbus API name
        """Return true: this response is an error."""

        return True


def put(bank: dict[int, int], spec: R.RegisterSpec, value: float | str) -> None:
    """Store a decoded value in a register bank."""

    if spec.datatype is R.RegisterDataType.STRING:
        raw = str(value).encode("utf-8").ljust(spec.count * 2, b"\x00")
        for index in range(spec.count):
            bank[spec.address + index] = (raw[index * 2] << 8) | raw[index * 2 + 1]
        return
    if spec.datatype is R.RegisterDataType.UINT32:
        number = int(value)
        bank[spec.address] = (number >> 16) & 0xFFFF
        bank[spec.address + 1] = number & 0xFFFF
        return
    for offset, word in enumerate(encode_register(spec, float(value))):
        bank[spec.address + offset] = word


def device_bank(
    *,
    layout: int = R.LAYOUT_V01_03,
    serial: str = "ABC123456789",
    article: str = "1313201205",
    firmware: str = "2023.21.11024",
    phase_options: int = 2,
    max_current: float = 16.0,
    evse_state: int = 1,
    charging_current: float = 16.0,
    error_code: int = 0,
    fallback_current: int = 6,
) -> dict[int, int]:
    """Return a register bank that looks like a validated 4You 310."""

    bank: dict[int, int] = {}
    bank[R.MODBUS_LAYOUT_VERSION.address] = layout
    put(bank, R.FIRMWARE_VERSION, firmware)
    put(bank, R.SERIAL_NUMBER, serial)
    put(bank, R.ARTICLE_NUMBER, article)
    bank[R.EVSE_STATE.address] = evse_state
    bank[R.AUTHORIZATION_STATUS.address] = 1
    bank[R.DOWNGRADE_STATUS.address] = 1
    bank[R.PHASE_ROTATION.address] = 0
    bank[R.CP_STATE.address] = 11
    put(bank, R.SIGNALED_CURRENT, 0.0)
    put(bank, R.DOWNGRADE_CURRENT, 8.0)
    put(bank, R.CHARGING_CURRENT_EMS, charging_current)
    put(bank, R.MAX_CURRENT_HOUSE, 32.0)
    put(bank, R.MAX_EVSE_CURRENT, max_current)
    bank[R.PHASE_SWITCHING_MODE.address] = 2
    bank[R.PHASE_OPTIONS_HW.address] = phase_options
    bank[R.CABLE_LOCK_SETTING.address] = 0
    bank[R.EMS_FALLBACK_CURRENT.address] = fallback_current
    bank[R.GRID_IMBALANCE.address] = 1
    bank[R.GRID_IMBALANCE_THRESHOLD.address] = 20
    bank[R.GRID_PHASES_CONNECTED.address] = 2
    bank[R.AUTHORIZATION_ENABLED.address] = 0
    bank[R.SOLAR_MIN_CURRENT.address] = 6
    bank[R.PHASE_SWITCHING_PAUSE.address] = 120
    for spec, value in (
        (R.CURRENT_L1, 0.0),
        (R.CURRENT_L2, 0.0),
        (R.CURRENT_L3, 0.0),
        (R.VOLTAGE_L1, 230.1),
        (R.VOLTAGE_L2, 229.8),
        (R.VOLTAGE_L3, 230.4),
        (R.POWER_L1, 0.0),
        (R.POWER_L2, 0.0),
        (R.POWER_L3, 0.0),
        (R.POWER_TOTAL, 0.0),
        (R.TEMPERATURE, 21.5),
        (R.SESSION_MAX_CURRENT, 16.0),
        (R.SESSION_ENERGY, 1.25),
        (R.ENERGY_TOTAL, 1234.5),
    ):
        put(bank, spec, value)
    put(bank, R.SESSION_DURATION, 600)
    put(bank, R.SESSIONS_TOTAL, 42)
    bank[R.DETECTED_EV_PHASES.address] = 3
    bank[R.CABLE_LOCK_STATUS.address] = 3
    bank[R.SOLAR_CHARGING_MODE.address] = 2
    bank[R.REQUESTED_PHASES.address] = 0
    bank[R.CHARGING_RELEASE.address] = 1
    bank[R.LOCK_EVSE.address] = 0
    bank[R.ERROR_CODE.address] = error_code
    bank[R.MASTER_LOST_FALLBACK.address] = 0
    bank[R.SWITCHED_PHASES.address] = 0
    return bank


def raise_connection_error() -> ConnectionException:
    """Return the pymodbus error for a dead serial link."""

    return ConnectionException("port closed")


def raise_modbus_error() -> ModbusException:
    """Return a generic pymodbus protocol error."""

    return ModbusException("bad frame")
