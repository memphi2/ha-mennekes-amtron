"""Typed subset of the Modbus transport and responses used by this integration."""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from typing import Protocol


class ModbusResponse(Protocol):
    """Response fields; runtime checks still reject malformed acknowledgements."""

    @property
    def function_code(self) -> int: ...

    @property
    def registers(self) -> Sequence[int]: ...

    @property
    def address(self) -> int: ...

    @property
    def count(self) -> int: ...

    def isError(self) -> bool: ...  # noqa: N802 - pymodbus API spelling


class ModbusTransport(Protocol):
    """Only the Modbus operations the integration is permitted to perform."""

    @property
    def connected(self) -> bool: ...

    async def connect(self) -> bool: ...

    def close(self) -> Awaitable[None] | None: ...

    def read_holding_registers(
        self, address: int, *, count: int, device_id: int
    ) -> Awaitable[ModbusResponse | None]: ...

    def write_register(
        self, address: int, value: int, *, device_id: int
    ) -> Awaitable[ModbusResponse | None]: ...

    def write_registers(
        self, address: int, values: list[int], *, device_id: int
    ) -> Awaitable[ModbusResponse | None]: ...
