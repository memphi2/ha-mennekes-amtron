"""What the control knows: options, requested state, goals and the one pending command."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from .client import MennekesModbusClient
from .client_errors import (
    AmtronIdentityError,
    AmtronIllegalAddressError,
    AmtronWriteRejectedError,
)
from .command_events import CommandEvents, command_source
from .const import (
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_UNLIMITED,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    MAX_PHASE_SWITCH_GRACE_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
)
from .data import DeviceIdentity, WallboxData, WriteDiagnostics
from .energy_goal import EnergyGoal
from .errors import DeviceValidationError, validation_error
from .plug_charge import PlugChargeController
from .registers import (
    CHARGING_CURRENT_EMS,
    ENERGY_TOTAL,
)
from .soc_goal import SocGoal
from .unplug_release import UnplugRelease
from .write_state import WriteState

if TYPE_CHECKING:
    from .control import AmtronControl
    from .energy_goal_controller import EnergyGoalController
    from .soc_goal_controller import SocGoalController


@dataclass(slots=True)
class _PhaseChange:
    confirmation_deadline: float
    ready_at: float | None = None
    unplug_confirmed: bool = False


class _PhaseWaitError(Exception):
    """Yield the command lock while the car pauses or the grace time passes."""

    def __init__(self, delay: float, *, preserve_deadline: bool = False) -> None:
        self.retry_after = delay
        self.preserve_deadline = preserve_deadline


class _TimedCommandCancelledError(Exception):
    """The trip or its charge target no longer authorizes a pending command."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _PendingCommand:
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


class ControlState:
    """The state every write decision reads; it writes nothing itself."""

    def __init__(
        self,
        client: MennekesModbusClient,
        *,
        diagnostics: WriteDiagnostics,
        identity: DeviceIdentity,
        options: ControlOptions,
        state: Callable[[], WallboxData],
        request_refresh: Callable[[], Awaitable[None]] | None = None,
        notify_write_error: Callable[[], None] | None = None,
        notify_pending: Callable[[], None] | None = None,
        validate_deferred: Callable[[], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._diagnostics = diagnostics
        self._identity = identity
        self._options = options
        self.startup_hold = False
        self._state = state
        self._request_refresh = request_refresh
        self._notify_write_error = notify_write_error
        self._notify_pending = notify_pending
        self._validate_deferred = validate_deferred
        self._sleep = sleep
        self._monotonic = monotonic
        self._pending: _PendingCommand | None = None
        self._phase_change: _PhaseChange | None = None
        self._release_writes = 0
        # When, and on which connection, a goal pause was last read back.
        self._goal_pause_confirmed: tuple[float, int] | None = None
        self._pending_due = 0.0
        self._pending_until: datetime | None = None
        self._pending_changed = asyncio.Event()
        self._current_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._write_state = client.write_state
        self._events = CommandEvents()
        self._active_command: _PendingCommand | None = None
        self._command_acknowledged = False
        self._guest_release_applied = False
        self._command_write_started = False
        self._recovery_deadline: float | None = None
        self._recovery_generation = 0
        self.goal_controller: EnergyGoalController | None = None
        self.soc_goal_controller: SocGoalController | None = None
        # Both work on the whole control, which is the only thing ever built.
        whole = cast("AmtronControl", self)
        self._plug_charge = PlugChargeController(whole)
        self.unplug_release = UnplugRelease(whole)
        self.guest_confirmation_required: Callable[[], bool] = lambda: False
        self.time_limit_reached: Callable[[], bool] = lambda: False
        self.guest_active: Callable[[], bool] = lambda: False

    @property
    def layout_version(self) -> int:
        return self._identity.layout_version

    def monotonic(self) -> float:
        """The clock of the write intervals."""
        return self._monotonic()

    @property
    def write_state(self) -> WriteState:
        """Share bounded control intent with the goal owners."""
        return self._write_state

    @property
    def energy_goal(self) -> EnergyGoal:
        """Return the shared durable energy limit, including its stop latch."""
        return self._write_state.energy_goal

    @property
    def supports_energy_goal(self) -> bool:
        """Energy goals require the wallbox's lifetime energy counter."""
        return self._identity.layout_version >= ENERGY_TOTAL.min_layout

    @property
    def goal_stop_requested(self) -> bool:
        """The energy and SoC goals' stop, including evidence awaiting the lock."""
        return (
            self.goal_controller.stop_requested if self.goal_controller is not None
            else self.energy_goal.stop_requested
        ) or self._soc_stop_requested

    @property
    def _soc_stop_requested(self) -> bool:
        """The active SoC fence, including pending evidence and storage faults."""
        return (
            self.soc_goal_controller.stop_requested if self.soc_goal_controller is not None
            else (not self.guest_active() and self.soc_goal.stop_requested)
        )

    @property
    def energy_goal_stop_requested(self) -> bool:
        """Every stop that fences starts: energy, vehicle percentage and guest time."""
        return self.goal_stop_requested or self.time_limit_reached() or self.guest_confirmation_required()

    @property
    def release_stop_requested(self) -> bool:
        """The same active limits also prevent a Plug & Charge grant."""
        return self.energy_goal_stop_requested

    def _stop_reason(self) -> str:
        """Prefer the retained energy/percentage goal when multiple limits apply."""
        if self.guest_confirmation_required():
            return "guest_confirmation"
        return "time_limit" if not self.goal_stop_requested and self.time_limit_reached() else "energy_goal"

    def stop_error(self) -> DeviceValidationError:
        if self.guest_confirmation_required():
            return validation_error("guest_confirmation_required")
        return validation_error(
            "time_limit_reached" if self._stop_reason() == "time_limit" else "energy_goal_stop_active"
        )

    @property
    def energy_goal_guard(self) -> asyncio.Lock:
        """Serialize the goal owner with ordinary and deferred commands."""
        return self._current_lock

    @property
    def soc_goal(self) -> SocGoal:
        """Return the vehicle percentage ceiling for the configured sources."""
        return self._write_state.soc_goal

    @property
    def supports_soc_goal(self) -> bool:
        return True

    @property
    def events(self) -> CommandEvents:
        """Expose discrete outcomes without per-poll entity updates."""
        return self._events

    @property
    def pending_action(self) -> str | None:
        """Return the latest queued command without changing actual readback."""
        return self._pending.action if self._pending else None

    @property
    def pending_guest_start(self) -> bool:
        """A queued resume that also owns the required release step."""
        return self._pending is not None and self._pending.grant_release

    @property
    def pending_owner(self) -> str | None:
        """The owner of the queued command; None for a command by hand."""
        return self._pending.owner if self._pending else None

    @property
    def pending_until(self) -> datetime | None:
        """Return a stable deadline; a UI can derive remaining time locally."""
        return self._pending_until

    @property
    def charging_setpoint(self) -> float | None:
        """Return the remembered resume target, retained across entry reloads."""

        return self._write_state.charging_setpoint

    @charging_setpoint.setter
    def charging_setpoint(self, value: float | None) -> None:
        self._write_state.charging_setpoint = value

    @property
    def charging_release_requested(self) -> bool | None:
        """Return explicit release intent, independently of fault readback."""

        return self._write_state.charging_release_requested

    @charging_release_requested.setter
    def charging_release_requested(self, value: bool | None) -> None:
        self._write_state.charging_release_requested = value

    @property
    def pause_requested(self) -> bool | None:
        """Return pause intent, independently of fault/reset readback."""

        return self._write_state.pause_requested

    @pause_requested.setter
    def pause_requested(self, value: bool | None) -> None:
        self._write_state.pause_requested = value

    @property
    def options(self) -> ControlOptions:
        """Return the active control options."""

        return self._options

    @property
    def read_only(self) -> bool:
        """Block commands; a startup hold still permits protective gate denial."""

        return self._options.read_only or self.startup_hold

    @property
    def max_charging_current(self) -> float:
        """Return the highest charging current a user may request.

        The device limits anything it is sent anyway; this is the limit the
        number entity shows, so a user never sees a value the wallbox would
        silently clamp.
        """

        limits = [
            value
            for value in (
                self._identity.max_evse_current,
                self._options.current_cap,
            )
            if value is not None and value >= CHARGING_CURRENT_MINIMUM
        ]
        if not limits:
            return CHARGING_CURRENT_MINIMUM
        return min(limits)

    @property
    def is_paused(self) -> bool | None:
        """Keep confirmed state across failed polls; unknown is not running."""

        snapshot = self._state()
        if snapshot.current_write_generation == self._write_state.current_write_generation:
            current = snapshot.get(CHARGING_CURRENT_EMS.key)
            if isinstance(current, (int, float)) and math.isfinite(current) and current >= 0:
                self._write_state.confirmed_current = float(current)
        return pause_state(self._write_state.confirmed_current)

    @staticmethod
    def _validate_timed_owner(owner: str) -> None:
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise ValueError("invalid timed command owner")

    @staticmethod
    def _uncertain(error: BaseException) -> bool:
        return not isinstance(error, DeviceValidationError) and not isinstance(
            error.__cause__,
            (AmtronWriteRejectedError, AmtronIllegalAddressError, AmtronIdentityError),
        )

    def _mode_change_remaining(self) -> float:
        if self._write_state.mode_change_at is None:
            return 0.0
        waited = self._monotonic() - self._write_state.mode_change_at
        return max(0.0, self._options.state_change_interval_seconds - waited)

    def _record_replaced(self, command: _PendingCommand | None) -> None:
        if self._pending is not None and self._pending != command:
            self.events.record("replaced", self._pending.action, "replaced", source=self._pending.source, requested_value=self._pending.value)

    def _clear_pending(self, *, outcome: str | None = None, reason: str = "completed") -> bool:
        changed = self._pending is not None
        if self._pending is not None:
            if outcome is not None and self._active_command is None:
                self.events.record(outcome, self._pending.action, reason, source=self._pending.source, requested_value=self._pending.value)
            elif self._active_command is not None:
                self._record_replaced(self._active_command)
        self._pending = None
        self._phase_change = None
        self._pending_until = None
        self._pending_changed.set()
        return changed

    def _command_cancellation_reason(self, command: _PendingCommand) -> str | None:
        if command.grant_release and not self.guest_active():
            return "unknown"
        if command.owner is None:
            return None
        if command.deadline is not None and datetime.now(UTC) >= command.deadline:
            return "deadline_expired"
        if self.soc_goal.status != "active" or self.soc_goal.target_percent <= 0 or self.energy_goal_stop_requested:
            return "energy_goal"
        return None

    def _check_command_context(self) -> None:
        command = self._active_command
        if command is not None and (reason := self._command_cancellation_reason(command)):
            raise _TimedCommandCancelledError(reason)

    def _require_write_access(self) -> None:
        if self.read_only:
            raise validation_error("read_only_mode")
