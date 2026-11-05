"""Small, passive diagnostics history; never part of entity or Recorder state."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

MAX_EVENTS = 40


class EventType(StrEnum):
    """Only predefined event names can enter the diagnostic history."""

    CONNECTION_LOST = "connection_lost"
    CONNECTION_RECOVERED = "connection_recovered"
    HEARTBEAT_FAILED = "heartbeat_failed"
    HEARTBEAT_RECOVERED = "heartbeat_recovered"
    HEARTBEAT_LATE = "heartbeat_late"
    WRITE_REJECTED = "write_rejected"
    WRITE_RATE_LIMITED = "write_rate_limited"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"


def sanitize_events(value: object) -> list[dict[str, str | float | int | None]] | None:
    """Copy only bounded, predefined event metadata from a remote service."""

    if not isinstance(value, list) or len(value) > MAX_EVENTS:
        return None
    events: list[dict[str, str | float | int | None]] = []
    names = {event.value for event in EventType}
    for row in value:
        if not isinstance(row, dict) or row.keys() != {
            "seconds_since_load", "event", "register_address",
        }:
            return None
        seconds, name, address = (
            row["seconds_since_load"], row["event"], row["register_address"]
        )
        if not (
            (type(seconds) is int and seconds >= 0)
            or (type(seconds) is float and math.isfinite(seconds) and seconds >= 0)
        ):
            return None
        if type(name) is not str or name not in names:
            return None
        if address is not None and (
            type(address) is not int or not 0 <= address <= 0xFFFF
        ):
            return None
        events.append(dict(row))
    return events


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """One event without values, paths, identifiers or raw exception text."""

    seconds: float
    event: EventType
    register: int | None


class EventTimeline:
    """Keep only the last forty observed transitions, scoped to one entry load."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._entries: deque[RuntimeEvent] = deque(maxlen=MAX_EVENTS)

    def record(self, event: EventType, *, register: int | None = None) -> None:
        """Append a bounded event without storing externally supplied text."""
        if not isinstance(event, EventType):
            raise ValueError("Expected a predefined runtime event")
        if register is not None and (type(register) is not int or not 0 <= register <= 0xFFFF):
            raise ValueError("Expected a Modbus register address")
        self._entries.append(RuntimeEvent(round(self._clock() - self._started, 3), event, register))

    def snapshot(self) -> list[dict[str, str | float | int | None]]:
        """Copy history for a diagnostic download without I/O or HA state changes."""
        return [{"seconds_since_load": entry.seconds, "event": entry.event.value,
                 "register_address": entry.register} for entry in self._entries]
