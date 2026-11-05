"""Bounded, passive metadata for uncertain writes and their recovery.

Only timings, counts and predefined outcomes are retained. Packet contents and
exception messages never enter the history, which is not entity/Recorder state.
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from pymodbus.exceptions import ModbusIOException

from .client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
    AmtronWriteRejectedError,
)

MAX_WRITE_EVENTS = 20
_OUTCOMES = frozenset({
    "cancelled", "connection_error", "rejected", "io_error", "protocol_error",
    "unexpected_error", "recovered",
})
_TIMINGS = frozenset({"seconds_since_start", "queue_wait_ms", "transaction_ms"})
_PACKET_TIMINGS = frozenset({"first_tx_ms_from_start", "last_rx_ms_from_start"})
_COUNTERS = frozenset({"tx_packets", "rx_callbacks"})
_FIELDS = _TIMINGS | _PACKET_TIMINGS | _COUNTERS | {
    "register_address", "outcome", "connected",
}

type TraceRecord = dict[str, int | float | str | bool | None]


@dataclass(slots=True)
class _PendingWrite:
    address: int
    queued: float
    started: float
    tx_packets: int = 0
    rx_callbacks: int = 0
    first_tx: float | None = None
    last_rx: float | None = None


def _outcome(error: BaseException) -> str:
    """Classify observed failures without inferring why a reply was absent."""

    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, AmtronConnectionError):
        return "connection_error"
    if isinstance(error, AmtronWriteRejectedError | AmtronIllegalAddressError):
        return "rejected"
    if isinstance(error, AmtronProtocolError) and isinstance(
        error.__cause__, ModbusIOException
    ):
        return "io_error"
    if isinstance(error, AmtronProtocolError):
        return "protocol_error"
    return "unexpected_error"


def _nonnegative_number(value: object) -> bool:
    return (type(value) is int and value >= 0) or (
        type(value) is float and math.isfinite(value) and value >= 0
    )


def sanitize_snapshot(value: object) -> list[TraceRecord] | None:
    """Accept only bounded, predefined metadata from the remote service."""

    if not isinstance(value, list) or len(value) > MAX_WRITE_EVENTS:
        return None
    records: list[TraceRecord] = []
    for row in value:
        if not isinstance(row, dict) or row.keys() != _FIELDS:
            return None
        address = row["register_address"]
        if type(address) is not int or not 0 <= address <= 0xFFFF:
            return None
        if (type(row["outcome"]) is not str or row["outcome"] not in _OUTCOMES
                or type(row["connected"]) is not bool):
            return None
        if any(not _nonnegative_number(row[key]) for key in _TIMINGS):
            return None
        if any(row[key] is not None and not _nonnegative_number(row[key])
               for key in _PACKET_TIMINGS):
            return None
        if any(row[key] is not None and (type(row[key]) is not int or row[key] < 0)
               for key in _COUNTERS):
            return None
        records.append(dict(row))
    return records


class WriteTrace:
    """Observe serialized writes without logging, I/O or background work."""

    def __init__(self, clock: Callable[[], float], packet_trace: bool) -> None:
        self._clock = clock
        self._started = clock()
        self._packet_trace = packet_trace
        self._pending: _PendingWrite | None = None
        self._failed: set[int] = set()
        self._events: deque[TraceRecord] = deque(maxlen=MAX_WRITE_EVENTS)

    def begin(self, *, address: int, queued: float, started: float) -> None:
        """Start observing a write after its caller has acquired the bus."""

        if type(address) is not int or not 0 <= address <= 0xFFFF:
            raise ValueError("Expected a Modbus register address")
        self._pending = _PendingWrite(address, queued, started)

    def packet(self, sending: bool, data: bytes) -> bytes:
        """Count callbacks for the active write and return bytes untouched."""

        pending = self._pending
        if self._packet_trace and pending is not None:
            now = self._clock()
            if sending:
                pending.tx_packets += 1
                if pending.first_tx is None:
                    pending.first_tx = now
            else:
                pending.rx_callbacks += 1
                pending.last_rx = now
        return data

    def finish(self, *, error: BaseException | None, connected: bool) -> None:
        """Retain failures and the first subsequent confirmation per register."""

        pending, self._pending = self._pending, None
        if pending is None:
            return
        if error is None:
            if pending.address not in self._failed:
                return
            self._failed.remove(pending.address)
            outcome = "recovered"
        else:
            self._failed.add(pending.address)
            outcome = _outcome(error)
        now = self._clock()

        def relative_ms(timestamp: float | None) -> float | None:
            if timestamp is None:
                return None
            return round(max(0.0, timestamp - pending.started) * 1000, 3)

        self._events.append({
            "seconds_since_start": round(max(0.0, now - self._started), 3),
            "register_address": pending.address,
            "outcome": outcome,
            "connected": connected,
            "queue_wait_ms": round(max(0.0, pending.started - pending.queued) * 1000, 3),
            "transaction_ms": relative_ms(now),
            "tx_packets": pending.tx_packets if self._packet_trace else None,
            "rx_callbacks": pending.rx_callbacks if self._packet_trace else None,
            "first_tx_ms_from_start": relative_ms(pending.first_tx),
            "last_rx_ms_from_start": relative_ms(pending.last_rx),
        })

    def snapshot(self) -> list[TraceRecord]:
        """Copy metadata without changing state or observing additional traffic."""

        return [dict(event) for event in self._events]
