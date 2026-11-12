"""Optional, short-lived decoded Modbus observations, kept only in bounded RAM.

Never observe transport packets: authentication and serial identities must not
reach the debug interface. Capturing does not schedule any bus transactions.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pymodbus.exceptions import ModbusIOException

from .client_errors import AmtronConnectionError, AmtronProtocolError
from .decode import decode_register, enum_option
from .register_monitor import RegisterMonitor
from .registers import HEARTBEAT, REGISTERS, SERIAL_NUMBER, RegisterDataType

MAX_RECORDS = 200
CAPTURE_SECONDS = 600
TraceStart = tuple[int, float]


class ModbusTrace:
    """Capture explicit ten-minute sessions; disabled by default and on restart."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.register_monitor: RegisterMonitor | None = None
        self._deadline = 0.0
        self._generation = 0
        self._sequence = 0
        self._records: deque[dict[str, Any]] = deque(maxlen=MAX_RECORDS)
        self._timer: asyncio.TimerHandle | None = None

    def set_enabled(self, enabled: bool) -> None:
        """Start once without extending an active session, or erase its data."""
        self._expire()
        if enabled and self._deadline:
            return
        self.close()
        if enabled:
            self._deadline = self._clock() + CAPTURE_SECONDS
            self._timer = asyncio.get_running_loop().call_later(CAPTURE_SECONDS, self.close)

    def close(self) -> None:
        """Stop capture and erase observations, also at the automatic deadline."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._deadline = 0.0
        self._generation += 1
        self._records.clear()

    def clear(self) -> None:
        """Erase rows without extending the deadline or accepting older requests."""
        self._records.clear()
        self._generation += 1

    def _expire(self) -> None:
        if self._deadline and self._clock() >= self._deadline:
            self.close()

    def start(self) -> TraceStart | None:
        """Mark attempts for enabled capture or the bounded passive register view."""
        self._expire()
        return (self._generation, self._clock()) if self._deadline or self.register_monitor is not None else None

    def finish(
        self, started: TraceStart | None, *, operation: str,
        address: int, count: int, device_id: int,
        words: Sequence[int] | None, error: BaseException | None,
    ) -> None:
        """Retain only register-map values and classified errors, never raw data."""
        category = None
        if error is not None:
            if isinstance(error, asyncio.CancelledError):
                category = "cancelled"
            elif isinstance(error, AmtronConnectionError):
                category = "connection"
            elif isinstance(error.__cause__, ModbusIOException):
                category = "timeout"
            elif isinstance(error, AmtronProtocolError):
                category = "device_exception" if error.exception_code is not None else "protocol"
            else:
                category = "unexpected"
        if self.register_monitor is not None:
            self.register_monitor.observe(
                operation=operation, address=address, count=count, words=words, error=category,
            )
        self._expire()
        if started is None or not self._deadline or started[0] != self._generation:
            return
        values: list[dict[str, Any]] = []
        if words is not None:
            for spec in REGISTERS:
                if spec.address >= address + count or spec.end_address <= address:
                    continue
                # Mask even partial serial reads and never decode arbitrary text.
                if spec == SERIAL_NUMBER or spec.datatype == RegisterDataType.STRING:
                    values.append({"register": spec.key, "address": f"0x{spec.address:04X}", "redacted": True})
                elif address <= spec.address and spec.end_address <= address + len(words):
                    section = words[spec.address - address:spec.end_address - address]
                    value = decode_register(spec, section) if all(type(word) is int and 0 <= word <= 0xFFFF for word in section) else None
                    values.append({
                        "register": spec.key, "address": f"0x{spec.address:04X}",
                        "value": value, "unit": spec.unit, "enum": enum_option(spec, value),
                    })
        self._sequence += 1
        self._records.append({
            "sequence": self._sequence,
            "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "operation": "heartbeat" if operation == "write" and address == HEARTBEAT.address else operation,
            "function": "0x03" if operation == "read" else ("0x06" if count == 1 else "0x10"),
            "address": f"0x{address:04X}", "count": count, "device_id": device_id,
            "duration_ms": round(max(0.0, self._clock() - started[1]) * 1000, 1),
            "outcome": "ok" if error is None else "error", "error": category,
            "exception_code": error.exception_code if isinstance(error, AmtronProtocolError) else None,
            "values": values,
        })

    def snapshot(self, *, after: int = 0) -> dict[str, Any]:
        """Return a bounded view without refreshing the capture's lifetime."""
        self._expire()
        return {
            "available": True, "enabled": bool(self._deadline),
            "expires_in": max(0, math.ceil(self._deadline - self._clock())) if self._deadline else 0,
            "latest_sequence": self._sequence, "generation": self._generation,
            "records": [record for record in self._records if record["sequence"] > after],
        }
