"""Runtime data structures for the MENNEKES AMTRON integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .decode import RegisterValue
from .registers import LAYOUT_V01_00

if TYPE_CHECKING:
    from .client import MennekesModbusClient
    from .control import AmtronControl
    from .coordinator import MennekesAmtronCoordinator
    from .heartbeat import HeartbeatTask


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
    writes_rate_limited: int = 0
    recoveries: int = 0
    last_error: str | None = None


@dataclass(slots=True)
class ConnectionState:
    """Availability of the serial connection, tracked for log hygiene."""

    available: bool = True
    logged_unavailable: bool = False

    def record(self, available: bool) -> bool:
        """Store availability and return true when the state changed."""

        changed = available != self.available
        self.available = available
        return changed


@dataclass(slots=True)
class MennekesAmtronRuntimeData:
    """Everything one loaded config entry owns."""

    client: MennekesModbusClient
    coordinator: MennekesAmtronCoordinator
    control: AmtronControl
    identity: DeviceIdentity
    diagnostics: WriteDiagnostics
    connection_state: ConnectionState
    heartbeat: HeartbeatTask | None = None
    # What the repair issues looked like last time, so an unchanged issue is
    # not written to the issue registry on every poll.
    repair_state: dict[str, dict[str, str] | None] = field(default_factory=dict)
