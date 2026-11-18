"""The small types the wallbox control and its parts share."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .command_events import command_source
from .const import (
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_UNLIMITED,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    MAX_PHASE_SWITCH_GRACE_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
)


@dataclass(slots=True)
class PhaseChange:
    confirmation_deadline: float
    ready_at: float | None = None
    unplug_confirmed: bool = False


class PhaseWaitError(Exception):
    """Yield the command lock while the car pauses or the grace time passes."""

    def __init__(self, delay: float, *, preserve_deadline: bool = False) -> None:
        self.retry_after = delay
        self.preserve_deadline = preserve_deadline


class TimedCommandCancelledError(Exception):
    """The trip or its charge target no longer authorizes a pending command."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PendingCommand:
    action: str
    value: float
    energy_goal_stop: bool = False
    grant_release: bool = False
    source: str = field(default_factory=command_source.get)
    completion_reason: str = "completed"
    recovery_generation: int | None = None
    owner: str | None = None
    deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class ControlOptions:
    """User-chosen limits on what the integration may do on the bus."""

    read_only: bool = True
    current_cap: float | None = None
    state_change_interval_seconds: float = DEFAULT_STATE_CHANGE_INTERVAL_SECONDS
    phase_switch_grace_seconds: float = DEFAULT_PHASE_SWITCH_GRACE_SECONDS

    def __post_init__(self) -> None:
        interval = self.state_change_interval_seconds
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or not 0 <= interval <= MAX_STATE_CHANGE_INTERVAL_SECONDS
        ):
            raise ValueError("state_change_interval_seconds must be between 0 and 3600")
        grace = self.phase_switch_grace_seconds
        if (
            isinstance(grace, bool)
            or not isinstance(grace, (int, float))
            or not math.isfinite(grace)
            or not 0 <= grace <= MAX_PHASE_SWITCH_GRACE_SECONDS
        ):
            raise ValueError("phase_switch_grace_seconds must be between 0 and 30")


def pause_state(value: object) -> bool | None:
    """Whether a signalled current is a pause; None without a valid reading."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return CHARGING_CURRENT_UNLIMITED < value < CHARGING_CURRENT_MINIMUM
