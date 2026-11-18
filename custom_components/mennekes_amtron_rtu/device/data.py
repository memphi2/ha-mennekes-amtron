"""Runtime data structures for the MENNEKES AMTRON integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .decode import RegisterValue
from .registers import LAYOUT_V01_00
from .runtime_events import EventTimeline


@dataclass(slots=True)
class DeviceIdentity:
    """Immutable device facts read once during setup."""

    layout_version: int = LAYOUT_V01_00
    firmware_version: str | None = None
    serial_number: str | None = None
    article_number: str | None = None
    phase_options_hw: int | None = None
    max_evse_current: float | None = None


@dataclass(slots=True)
class WallboxData:
    """One coordinator snapshot of the device."""

    values: dict[str, RegisterValue] = field(default_factory=dict)
    failed_blocks: tuple[str, ...] = ()
    # Presentation only; controllers continue to use register values.
    charging_display: str | None = None
    # Internal ordering only: never make a poll notify entities merely because
    # its generation changed, and never expose this in entity attributes.
    current_write_generation: int = field(default=0, compare=False, repr=False)
    # When a queued command is due, so its outcome is read then.
    pending_change_until: datetime | None = field(default=None, repr=False)
    # What the service decided apart from registers: departure, operation,
    # guest charging, Plug & Charge. Entities show it, so a change notifies.
    service_control: dict[str, Any] | None = field(default=None, repr=False)
    # Only quality transitions notify entities. Cumulative transaction counts
    # remain fresh for diagnostics without waking every entity on each retry.
    connection_quality: str | None = None
    connection_failures: int | None = field(default=None, compare=False)
    connection_retries: int | None = field(default=None, compare=False)
    # Goal settings/latches change independently of register readback.
    energy_goal: dict[str, float | str | None] | None = field(default=None, repr=False)
    soc_goal: dict[str, float | str | None] | None = field(default=None, repr=False)
    # A goal may only act on a counter attempted in this poll, never a cached one.
    energy_read: bool = field(default=False, compare=False, repr=False)
    # Actual meter attempt time, never the time of a repeated API/cache read.
    energy_observed_at: float | None = field(default=None, compare=False, repr=False)
    # The session clock and energy were read in this poll, not carried forward.
    session_read: bool = field(default=False, compare=False, repr=False)
    energy_goal_generation: int = field(default=0, compare=False, repr=False)
    # Only completed-session metadata changes; no per-poll progress is exposed.
    # Keys kept from an earlier poll across one failed read, for display only.
    held: frozenset[str] = field(default=frozenset(), compare=False, repr=False)

    def get(self, key: str) -> RegisterValue:
        """Return one decoded register value, or ``None`` when unavailable."""

        return self.values.get(key)

    def has(self, key: str) -> bool:
        """Return true when this snapshot carries a value for a key."""

        return self.values.get(key) is not None

    def fresh(self, key: str) -> RegisterValue:
        """Return a value read in this poll; safety decisions never use a held one."""

        return None if key in self.held else self.values.get(key)


@dataclass(slots=True)
class WriteDiagnostics:
    """Counters describing how the integration behaved on the bus."""

    heartbeats_sent: int = 0
    heartbeats_failed: int = 0
    heartbeats_late: int = 0
    writes_sent: int = 0
    writes_rejected: int = 0
    write_errors: set[str] = field(default_factory=set)
    writes_rate_limited: int = 0
    recoveries: int = 0
    last_error: str | None = None
    timeline: EventTimeline = field(default_factory=EventTimeline)


@dataclass(slots=True)
class ConnectionState:
    """Availability of the Modbus connection, so each change is recorded once."""

    available: bool = True

    def record(self, available: bool) -> bool:
        """Store availability and return true when the state changed."""

        changed = available != self.available
        self.available = available
        return changed
