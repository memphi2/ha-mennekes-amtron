"""Bounded, passive register observations; requests never schedule Modbus traffic."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .decode import RegisterValue, decode_register, enum_option
from .register_blocks import REGISTER_BLOCKS, SLOW_BLOCK_INTERVAL_SECONDS, BlockCadence
from .registers import REGISTERS, SERIAL_NUMBER, RegisterSpec

_CADENCES = {key: block.cadence for block in REGISTER_BLOCKS for key in block.keys}


@dataclass(slots=True)
class _Reading:
    words: tuple[int, ...] | None = None
    value: RegisterValue = None
    read_at: str | None = None
    read_clock: float | None = None
    attempted_at: str | None = None
    error: str | None = None
    failures: int = 0
    write_at: str | None = None
    write_error: str | None = None


class RegisterMonitor:
    """Keep one last valid sample per known register; never store login frames."""

    def __init__(
        self, *, monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = monotonic
        self._utcnow = utcnow
        self._readings: dict[str, _Reading] = {}

    def clear(self) -> None:
        """Do not carry readings across a newly initialized device identity."""
        self._readings.clear()

    def observe(
        self, *, operation: str, address: int, count: int,
        words: Sequence[int] | None, error: str | None,
    ) -> None:
        """Consume validated transaction boundaries, not an arbitrary packet tap."""
        if operation not in ("read", "write") or count < 1:
            return
        now = self._clock()
        stamp = self._utcnow().isoformat(timespec="milliseconds")
        for spec in REGISTERS:
            if spec.address < address or spec.end_address > address + count:
                continue
            reading = self._readings.setdefault(spec.key, _Reading())
            if operation == "write":
                reading.write_at, reading.write_error = stamp, error
                # An acknowledgement is not a readback. In particular, a
                # requested current never replaces its last measured value.
                continue
            reading.attempted_at = stamp
            if error is not None:
                reading.error = error
                reading.failures = min(65535, reading.failures + 1)
                continue
            offset = spec.address - address
            section = None if words is None else words[offset:offset + spec.count]
            if section is None or len(section) != spec.count or any(
                type(word) is not int or not 0 <= word <= 65535 for word in section
            ):
                reading.error = "invalid_response"
                reading.failures = min(65535, reading.failures + 1)
                continue
            value = decode_register(spec, section)
            if value is None:
                reading.error = "invalid_value"
                reading.failures = min(65535, reading.failures + 1)
                continue
            # Identity data stays in the existing identity API; this support
            # screen and its raw-word cache deliberately redact the serial.
            reading.words = None if spec == SERIAL_NUMBER else tuple(section)
            reading.value = None if spec == SERIAL_NUMBER else value
            reading.read_at, reading.read_clock = stamp, now
            reading.error, reading.failures = None, 0

    @staticmethod
    def _interval(spec: RegisterSpec, scan: float, measurement: float, idle: bool) -> float | None:
        cadence = _CADENCES.get(spec.key)
        if cadence is None:
            return None
        if cadence is BlockCadence.FAST:
            return scan
        if cadence is BlockCadence.ACTIVE:
            return max(scan, SLOW_BLOCK_INTERVAL_SECONDS, measurement) if idle else max(scan, measurement)
        return max(scan, SLOW_BLOCK_INTERVAL_SECONDS)

    def snapshot(
        self, *, layout: int | None, available: bool, scan_interval: float,
        measurement_interval: float, idle: bool,
    ) -> dict[str, Any]:
        """The status describes observation quality, not the register's meaning.

        A successfully read nonzero wallbox error code is an OK observation.
        Failed/stale samples are retained with their original read timestamp.
        Every specified register is represented, including unsupported/write-only.
        """
        rows: list[dict[str, Any]] = []
        now = self._clock()
        for spec in REGISTERS:
            reading = self._readings.get(spec.key, _Reading())
            age = None if reading.read_clock is None else max(0.0, now - reading.read_clock)
            interval = self._interval(spec, scan_interval, measurement_interval, idle)
            stale_after = max(30, interval * 3) if interval is not None else None
            if layout is None:
                status, reason = "unknown", "layout_unknown"
            elif spec.min_layout > layout:
                status, reason = "unsupported", "layout_unsupported"
            elif not spec.readable:
                status, reason = "write_only", "no_readback"
            elif reading.read_clock is None:
                status, reason = ("error", reading.error) if reading.error else ("unknown", "not_read")
            elif not available:
                status, reason = "error", "wallbox_unavailable"
            elif reading.error:
                status = "degraded" if age is not None and (stale_after is None or age <= stale_after) else "error"
                reason = reading.error
            elif age is not None and stale_after is not None and age > stale_after:
                status, reason = "degraded", "overdue"
            elif spec.enum_map is not None and enum_option(spec, reading.value) is None:
                status, reason = "degraded", "unknown_enum"
            else:
                status, reason = "ok", None
            rows.append({
                "key": spec.key, "address": spec.address, "count": spec.count,
                "datatype": spec.datatype.value, "access": spec.access.value,
                "min_layout": spec.min_layout, "unit": spec.unit,
                "raw": list(reading.words) if reading.words is not None else None,
                "value": reading.value, "enum": enum_option(spec, reading.value),
                "updated_at": reading.read_at, "attempted_at": reading.attempted_at,
                "age_seconds": round(age, 1) if age is not None else None,
                "expected_interval_seconds": interval, "status": status, "reason": reason,
                "consecutive_failures": reading.failures,
                "write_attempted_at": reading.write_at, "write_error": reading.write_error,
                "redacted": spec == SERIAL_NUMBER,
            })
        return {"schema": 1, "available": available, "layout": layout, "registers": rows}
