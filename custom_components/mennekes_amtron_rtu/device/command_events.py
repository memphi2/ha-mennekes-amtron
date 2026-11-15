"""Small, transient command-result feed shared by both connection modes.

Only command outcomes enter this feed. Polls, heartbeats and countdown ticks
never create events. A new consumer establishes a cursor without replaying
historical commands; reconnecting does the same.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from uuid import uuid4

_LOGGER = logging.getLogger(__name__)
MAX_EVENTS = 32
SOURCES = ("unknown", "home_assistant", "app", "plug_charge", "departure_plan", "energy_goal", "soc_goal", "recovery", "guest")
command_source: ContextVar[str] = ContextVar("amtron_command_source", default="unknown")


def attributed[**P, T](source: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Coroutine[object, object, T]]]:
    """Scope provenance to this task, including calls through the service API."""
    def decorate(method: Callable[P, Awaitable[T]]) -> Callable[P, Coroutine[object, object, T]]:
        @wraps(method)
        async def call(*args: P.args, **kwargs: P.kwargs) -> T:
            token = command_source.set(source)
            try:
                return await method(*args, **kwargs)
            finally:
                command_source.reset(token)
        return call
    return decorate

CONTROL_OUTCOMES = ("executed", "replaced", "cancelled", "failed", "uncertain")
RECOVERY_OUTCOMES = ("recovery_succeeded", "recovery_failed", "uncertain")
OUTCOMES = (*CONTROL_OUTCOMES, "recovery_succeeded", "recovery_failed")
CONTROL_ACTIONS = (
    "pause", "resume", "set_current", "single_phase", "all_phases",
    "disable_charging", "enable_charging", "lock", "unlock",
)
ACTIONS = (*CONTROL_ACTIONS, "recover")


def event_type(action: str, outcome: str) -> str:
    """One type per command and outcome, so HA shows which command it was."""
    if action == "recover":
        return f"recovery_{outcome.removeprefix('recovery_')}"
    return f"{action}_{outcome}"


EVENT_TYPES = (
    *(event_type(action, outcome) for action in CONTROL_ACTIONS for outcome in CONTROL_OUTCOMES),
    *(event_type("recover", outcome) for outcome in RECOVERY_OUTCOMES),
)
_EVENT_TYPES = frozenset(EVENT_TYPES)


def _valid(action: object, outcome: object, reason: object) -> bool:
    return (
        action in ACTIONS and outcome in OUTCOMES and reason in REASONS
        and event_type(str(action), str(outcome)) in _EVENT_TYPES
    )
REASONS = (
    "completed", "replaced", "shutdown", "phase_timeout", "phase_unconfirmed",
    "connection_error", "write_rejected", "permission_denied",
    "status_refresh_failed", "unknown", "error_still_active", "user_cancelled", "energy_goal", "time_limit", "guest_confirmation",
    "deadline_expired", "vehicle_authorized", "vehicle_waiting",
)


@dataclass(frozen=True, slots=True)
class CommandEvent:
    """Allowlisted attributes, deliberately excluding exception messages."""

    sequence: int
    action: str
    outcome: str
    reason: str
    source: str = "unknown"
    occurred_at: str | None = None
    requested_value: float | None = None


class CommandEvents:
    """Bounded producer and deduplicating remote consumer."""

    def __init__(self, *, supported: bool = True) -> None:
        self.supported = supported
        self._boot_id = uuid4().hex
        self._sequence = 0
        self._events: deque[CommandEvent] = deque(maxlen=MAX_EVENTS)
        self._listeners: set[Callable[[CommandEvent], None]] = set()

    def listen(self, callback: Callable[[CommandEvent], None]) -> Callable[[], None]:
        """Subscribe to future events only, never the retained buffer."""
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def record(self, outcome: str, action: str, reason: str = "completed", *,
               source: str | None = None, requested_value: float | None = None) -> None:
        """Publish one outcome, without creating a persisted device history."""
        if not _valid(action, outcome, reason):
            raise ValueError("invalid command event")
        source = source or command_source.get()
        if source == "plug_charge" and reason == "completed" and outcome == "executed":
            reason = "vehicle_waiting" if action == "disable_charging" else "vehicle_authorized"
        self._sequence += 1
        event = CommandEvent(self._sequence, action, outcome, reason,
                             source or command_source.get(), datetime.now(UTC).isoformat(), requested_value)
        self._events.append(event)
        self._publish(event)

    def _publish(self, event: CommandEvent) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - observers must never alter a bus command's result
                _LOGGER.error("Unable to publish a command result to an observer")

    def snapshot(self) -> dict[str, object]:
        """Include a boot-scoped cursor so repeated snapshots are harmless."""
        return {
            "boot_id": self._boot_id,
            "sequence": self._sequence,
            "events": [asdict(event) for event in self._events],
        }

    @staticmethod
    def parse(raw: object) -> tuple[str, int, list[CommandEvent]] | None:
        """Allow a null feed during service initialization; validate live metadata."""
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("invalid command events")
        boot_id, sequence, rows = raw.get("boot_id"), raw.get("sequence"), raw.get("events")
        if (
            not isinstance(boot_id, str) or len(boot_id) != 32
            or any(char not in "0123456789abcdef" for char in boot_id)
            or type(sequence) is not int or not 0 <= sequence < 2**63
            or not isinstance(rows, list) or len(rows) > MAX_EVENTS
        ):
            raise ValueError("invalid command event cursor")
        events = []
        previous = 0
        for row in rows:
            if not isinstance(row, dict) or row.keys() != {"sequence", "action", "outcome", "reason", "source", "occurred_at", "requested_value"}:
                raise ValueError("invalid command event fields")
            index = row["sequence"]
            if (
                type(index) is not int or not previous < index <= sequence
                or not _valid(row["action"], row["outcome"], row["reason"])
            ):
                raise ValueError("invalid command event value")
            source = row.get("source", "unknown")
            occurred_at = row.get("occurred_at")
            value = row.get("requested_value")
            if source not in SOURCES or (value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 32)):
                raise ValueError("invalid command event details")
            if occurred_at is not None and (
                not isinstance(occurred_at, str) or len(occurred_at) > 40
                or datetime.fromisoformat(occurred_at).tzinfo is None
            ):
                raise ValueError("invalid command event time")
            events.append(CommandEvent(index, row["action"], row["outcome"], row["reason"], source, occurred_at, value))
            previous = index
        if (events and previous != sequence) or (not events and sequence != 0):
            raise ValueError("incomplete command event cursor")
        return boot_id, sequence, events

    def receive(
        self, parsed: tuple[str, int, list[CommandEvent]] | None, *, connected: bool,
    ) -> None:
        """Baseline a new connection or boot; otherwise publish new events once."""
        if parsed is None:
            self.supported = False
            return
        boot_id, sequence, events = parsed
        previous = self._sequence
        deliver = connected and self.supported and self._boot_id == boot_id
        if deliver and sequence < previous:
            raise ValueError("command event cursor moved backwards")
        self._boot_id, self._sequence = boot_id, sequence
        self.supported = True
        self._events = deque(events, maxlen=MAX_EVENTS)
        if deliver:
            for event in events:
                if event.sequence > previous:
                    self._publish(event)
