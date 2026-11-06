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
    # Internal ordering only: never make a poll notify entities merely because
    # its generation changed, and never expose this in entity attributes.
    current_write_generation: int = field(default=0, compare=False, repr=False)
    # A service permission change must notify controls even with identical
    # register values. This is internal metadata, never an entity attribute.
    service_read_only: bool | None = field(default=None, repr=False)
    service_supports_cancel_pending: bool = field(default=False, repr=False)
    # The service can change the slider range or retained paused setpoint
    # without changing a register. Compare these stable control values too.
    service_max_charging_current: float | None = field(default=None, repr=False)
    service_charging_setpoint: float | None = field(default=None, repr=False)
    # Fixed queue metadata changes only when intent changes, never each second.
    pending_change: str | None = field(default=None, repr=False)
    pending_change_until: datetime | None = field(default=None, repr=False)
    # Stable communication-quality enum, never transaction counters/timestamps.
    connection_quality: str | None = None
    # Goal settings/latches change independently of register readback.
    energy_goal: dict[str, float | str | None] | None = field(default=None, repr=False)
    soc_goal: dict[str, float | str | None] | None = field(default=None, repr=False)
    # A goal may only act on a counter attempted in this poll, never a cached one.
    energy_read: bool = field(default=False, compare=False, repr=False)
    # Actual service-side meter attempt time, not a repeated API cache read.
    energy_observed_at: float | None = field(default=None, compare=False, repr=False)
    energy_goal_generation: int = field(default=0, compare=False, repr=False)
    # Only completed-session metadata changes; no per-poll progress is exposed.
    charging_session: dict[str, Any] | None = field(default=None, repr=False)
    session_summary_supported: bool = field(default=False, repr=False)

    def get(self, key: str) -> RegisterValue:
        """Return one decoded register value, or ``None`` when unavailable."""

        return self.values.get(key)

    def has(self, key: str) -> bool:
        """Return true when this snapshot carries a value for a key."""

        return self.values.get(key) is not None


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
    """Availability of the Modbus connection, tracked for log hygiene."""

    available: bool = True
    logged_unavailable: bool = False

    def record(self, available: bool) -> bool:
        """Store availability and return true when the state changed."""

        changed = available != self.available
        self.available = available
        return changed
