"""The single write path to the wallbox.

Every state-changing operation goes through this module. That is deliberate:
0x0302 has three different meanings depending on the value written, and two
of them are dangerous.

    0            no limitation -- the wallbox signals its maximum (16/32 A)
    0.01 - 5.99  invalid -- the wallbox signals 0 A, the documented pause
    >= 6         a real charging-current limit

The current-limit input explicitly accepts zero as "limit off". Direct action
calls require allow_unlimited; pausing uses the dedicated pause command.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .capabilities import phase_switch_is_supported
from .client import MennekesModbusClient
from .client_errors import (
    AmtronBusError,
    AmtronIdentityError,
    AmtronIllegalAddressError,
    AmtronRateLimitedError,
    AmtronWriteRejectedError,
)
from .command_events import CONTROL_ACTIONS, CommandEvents
from .const import (
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_UNLIMITED,
    CHARGING_PAUSE_CURRENT,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    ERROR_CODE_EMS_UNAVAILABLE,
    HEARTBEAT_VALUE,
    MAX_PHASE_SWITCH_GRACE_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
    MIN_PHASE_CHANGE_INTERVAL_SECONDS,
    SYSTEM_RESTART_VALUE,
)
from .data import DeviceIdentity, WallboxData, WriteDiagnostics
from .energy_goal import EnergyGoal
from .enums import CpState, EvseState, RequestedPhases, SolarChargingMode
from .errors import DeviceError, DeviceValidationError, device_error, validation_error
from .registers import (
    CHARGING_CURRENT_EMS,
    CHARGING_RELEASE,
    CP_STATE,
    ENERGY_TOTAL,
    ERROR_CODE,
    EVSE_STATE,
    HEARTBEAT,
    LOCK_EVSE,
    REQUESTED_PHASES,
    SOLAR_CHARGING_MODE,
    SYSTEM_RESTART,
    RegisterSpec,
)
from .runtime_events import EventType
from .soc_goal import SocGoal
from .write_state import WriteState

if TYPE_CHECKING:
    from .energy_goal_controller import EnergyGoalController
    from .soc_goal_controller import SocGoalController

_LOGGER = logging.getLogger(__name__)
PENDING_ACTIONS = CONTROL_ACTIONS
_PHASE_PAUSE_TIMEOUT = 30.0


@dataclass(slots=True)
class _PhaseChange:
    confirmation_deadline: float
    ready_at: float | None = None


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


class AmtronControl:
    """Validated, rate-limited write access to one wallbox."""

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
        self._state = state
        self._request_refresh = request_refresh
        self._notify_write_error = notify_write_error
        self._notify_pending = notify_pending
        self._validate_deferred = validate_deferred
        self._sleep = sleep
        self._monotonic = monotonic
        self._pending: _PendingCommand | None = None
        self._phase_change: _PhaseChange | None = None
        self._pending_due = 0.0
        self._pending_until: datetime | None = None
        self._pending_changed = asyncio.Event()
        self._current_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._write_state = client.write_state
        self._events = CommandEvents()
        self._active_command: _PendingCommand | None = None
        self._command_acknowledged = False
        self._command_write_started = False
        self._recovery_deadline: float | None = None
        self._recovery_generation = 0
        self.goal_controller: EnergyGoalController | None = None
        self.soc_goal_controller: SocGoalController | None = None

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
    def energy_goal_stop_requested(self) -> bool:
        """Include already observed stop evidence waiting for the command lock."""
        return (
            self.goal_controller.stop_requested if self.goal_controller is not None
            else self.energy_goal.stop_requested
        ) or (
            self.soc_goal_controller.stop_requested if self.soc_goal_controller is not None
            else self.soc_goal.stop_requested
        )

    @property
    def energy_goal_guard(self) -> asyncio.Lock:
        """Serialize the goal owner with ordinary and deferred commands."""
        return self._current_lock

    async def async_set_energy_goal(self, value: float) -> None:
        """Change the energy ceiling without starting or resuming charging."""
        if self.goal_controller is None or not self.supports_energy_goal:
            raise validation_error("energy_goal_not_supported")
        await self.goal_controller.async_set_target(value)

    async def async_pause_for_energy_goal(self) -> None:
        """Enforce a latched energy stop, retaining only the current-write floor."""
        async with self._current_lock:
            if not self.energy_goal_stop_requested:
                return
            self._require_write_access()
            self._clear_pending(outcome="cancelled", reason="energy_goal")
            if self.is_paused is True:
                self.pause_requested = True
                return
            await self._async_observe_command(_PendingCommand(
                "pause", CHARGING_PAUSE_CURRENT, energy_goal_stop=True,
            ))

    @property
    def soc_goal(self) -> SocGoal:
        """Return the vehicle percentage ceiling for the configured sources."""
        return self._write_state.soc_goal

    @property
    def supports_soc_goal(self) -> bool:
        return True

    async def async_configure_soc(self, source: str | None) -> None:
        if self.soc_goal_controller is None:
            raise validation_error("soc_goal_not_supported")
        await self.soc_goal_controller.async_configure_source(source)

    async def async_set_soc_goal(
        self, value: float, *, source: str, soc: float | None = None,
        maximum: float | None = None,
    ) -> None:
        if self.soc_goal_controller is None:
            raise validation_error("soc_goal_not_supported")
        await self.soc_goal_controller.async_set_target(value, source=source, soc=soc, maximum=maximum)

    async def async_observe_soc(
        self, soc: float | None, maximum: float | None = None, *, source: str,
    ) -> None:
        if self.soc_goal_controller is None:
            raise validation_error("soc_goal_not_supported")
        await self.soc_goal_controller.async_observe(soc, maximum, source=source)

    def cancel_goal_resume(self) -> None:
        """Invalidate automatic requests calculated for a previous goal."""
        if self._pending is not None and (
            self._pending.recovery_generation is not None or self._pending.owner is not None
        ):
            self._clear_pending(outcome="cancelled", reason="energy_goal")
            if self._notify_pending is not None:
                self._notify_pending()

    async def async_energy_goal_recovery_ready(self) -> bool:
        """Confirm a cleared error before restoring only our own outage pause."""
        if self.read_only or self.energy_goal_stop_requested:
            return False
        return await self._client.async_read_register(ERROR_CODE) == 0

    async def async_resume_for_energy_goal_locked(self, generation: int) -> None:
        """Resume our outage pause while the caller owns the command lock."""
        self._require_write_access()
        maximum = self.max_charging_current
        target = self.charging_setpoint
        if target is None:
            target = maximum
        elif target != CHARGING_CURRENT_UNLIMITED:
            target = max(CHARGING_CURRENT_MINIMUM, min(target, maximum))
        await self._async_submit_locked(
            _PendingCommand("resume", target, recovery_generation=generation), automatic=True,
        )

    @property
    def events(self) -> CommandEvents:
        """Expose discrete outcomes without per-poll entity updates."""
        return self._events

    def observe_recovery(self, error_code: object, current_write_generation: int) -> None:
        """Confirm a completed recovery from a subsequent successful fresh poll."""
        if (
            self._recovery_deadline is None or type(error_code) is not int
            or current_write_generation < self._recovery_generation
        ):
            return
        if error_code == 0:
            self.events.record("recovery_succeeded", "recover")
        elif self._monotonic() >= self._recovery_deadline:
            self.events.record("recovery_failed", "recover", "error_still_active")
        else:
            return
        self._recovery_deadline = None

    @property
    def pending_action(self) -> str | None:
        """Return the latest queued command without changing actual readback."""
        return self._pending.action if self._pending else None

    @property
    def pending_until(self) -> datetime | None:
        """Return a stable deadline; a UI can derive remaining time locally."""
        return self._pending_until

    @property
    def supports_cancel_pending(self) -> bool:
        """This controller can remove queued work without touching the wallbox."""
        return True

    @property
    def can_cancel_pending(self) -> bool:
        """Cancellation needs permission and pending work, not a healthy bus."""
        return self.supports_cancel_pending and not self.read_only and self.pending_action is not None

    async def async_cancel_pending_change(self) -> None:
        """Discard future work, retaining any pause or setpoint already applied."""
        async with self._current_lock:
            self._require_write_access()
            changed = self._clear_pending(outcome="cancelled", reason="user_cancelled")
            if changed and self._notify_pending is not None:
                self._notify_pending()

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
        """Return true while the integration must not write to the bus."""

        return self._options.read_only

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
        value = self._write_state.confirmed_current
        if value is None:
            return None
        return CHARGING_CURRENT_UNLIMITED < float(value) < CHARGING_CURRENT_MINIMUM

    async def async_set_charging_current(
        self,
        value: float,
        *,
        allow_unlimited: bool = False,
    ) -> None:
        """Set the EMS charging-current limit in 0x0302.

        0 A does not mean "no charging" on this device: it disables the limit
        and makes the EVSE signal its maximum. 0.01-5.99 A is the vendor's
        documented way to pause. Both are therefore reachable only with an
        explicit opt-in or through :meth:`async_pause_charging`.
        """

        self._require_write_access()
        current = float(value)
        if current == CHARGING_CURRENT_UNLIMITED:
            if not allow_unlimited:
                raise validation_error("charging_current_zero")
            _LOGGER.info("Disabling the EMS current limit (0 A)")
        elif not math.isfinite(current) or current < CHARGING_CURRENT_MINIMUM:
            raise validation_error(
                "charging_current_invalid",
                {
                    "value": f"{current:.2f}",
                    "minimum": f"{CHARGING_CURRENT_MINIMUM:.0f}",
                },
            )
        else:
            current = min(current, self.max_charging_current)
        # Both a valid limit and zero can resume a paused charge. Classify
        # them from confirmed state, not from the name of the HA control.
        async with self._current_lock:
            await self._async_submit_locked(_PendingCommand("set_current", current))

    async def async_pause_charging(self) -> None:
        """Pause charging the documented way, by signalling 0 A to the EV."""

        self._require_write_access()
        async with self._current_lock:
            await self._async_submit_locked(_PendingCommand("pause", CHARGING_PAUSE_CURRENT))

    async def async_set_timed_current(
        self, value: float | None, *, deadline: datetime, owner: str,
    ) -> None:
        """Apply a trip's current without leaving an unbounded queued command."""
        self._require_write_access()
        self._validate_timed_owner(owner)
        if not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("timed command deadline needs a timezone")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < CHARGING_CURRENT_MINIMUM
        ):
            raise validation_error("charging_current_invalid", {"value": str(value), "minimum": "6"})
        command = _PendingCommand(
            "pause" if value is None else "set_current",
            CHARGING_PAUSE_CURRENT if value is None else min(value, self.max_charging_current),
            owner=owner, deadline=deadline,
        )
        async with self._current_lock:
            # A manual request can arrive while the caller waits for this lock.
            if self._pending is not None and self._pending.owner != owner:
                return
            if reason := self._timed_cancellation_reason(command):
                # Refused plans must not change remembered current or command
                # intent, or cancel another goal's automatic recovery.
                self.events.record("cancelled", command.action, reason)
                await self._async_cancel_pending()
                return
            await self._async_submit_locked(command)

    @staticmethod
    def _validate_timed_owner(owner: str) -> None:
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise ValueError("invalid timed command owner")

    async def async_cancel_timed_current(self, owner: str) -> None:
        """Cancel only this planner's queue, even when writes became disabled."""
        self._validate_timed_owner(owner)
        async with self._current_lock:
            if self._pending is not None and self._pending.owner == owner:
                self._clear_pending(outcome="cancelled", reason="user_cancelled")
                if self._notify_pending is not None:
                    self._notify_pending()

    def _resume_current_maximum(self, current_cap: float | None) -> float:
        if current_cap is None:
            return self.max_charging_current
        if not math.isfinite(current_cap) or current_cap < CHARGING_CURRENT_MINIMUM:
            raise validation_error(
                "charging_current_invalid",
                {"value": str(current_cap), "minimum": str(CHARGING_CURRENT_MINIMUM)},
            )
        return min(current_cap, self.max_charging_current)

    async def async_resume_charging(self, *, current_cap: float | None = None) -> None:
        """Resume charging with the setpoint that was active before the pause."""

        self._require_write_access()
        async with self._current_lock:
            maximum = self._resume_current_maximum(current_cap)
            target = self.charging_setpoint
            if target is None:
                target = maximum
            elif target != CHARGING_CURRENT_UNLIMITED:
                target = max(CHARGING_CURRENT_MINIMUM, min(target, maximum))
            await self._async_submit_locked(_PendingCommand("resume", target))

    async def async_set_charging_release(self, released: bool) -> None:
        """Open or close the charging relay through 0x0D05."""

        self._require_write_access()
        async with self._current_lock:
            await self._async_submit_locked(_PendingCommand(
                "enable_charging" if released else "disable_charging", int(released),
            ))

    async def async_set_lock_evse(self, locked: bool) -> None:
        """Lock or unlock the wallbox through 0x0D06."""

        self._require_write_access()
        async with self._current_lock:
            await self._async_submit_locked(_PendingCommand("lock" if locked else "unlock", int(locked)))

    async def async_set_solar_charging_mode(self, mode: SolarChargingMode) -> None:
        """Set the solar charging mode shown on the wallbox HMI (0x0D03)."""

        self._require_write_access()
        # Command context belongs to the operation holding this lock. An
        # unrelated solar setting must not inherit a timed current's expiry
        # or goal cancellation while that current awaits the shared bus.
        async with self._current_lock:
            await self._async_write(SOLAR_CHARGING_MODE, int(mode))

    async def async_set_requested_phases(self, phases: RequestedPhases) -> None:
        """Request single-phase or all-phase charging through 0x0D04.

        While the wallbox is charging, the current is set to the documented
        pause value first: the vendor's own sequence does that, and some EVs
        need it before the phase count changes.
        """

        self._require_write_access()
        if not phase_switch_is_supported(self._identity.phase_options_hw):
            raise validation_error("phase_switch_unsupported")
        async with self._current_lock:
            await self._async_submit_locked(_PendingCommand(
                "single_phase" if phases == RequestedPhases.SINGLE_PHASE else "all_phases",
                int(phases),
            ))

    async def async_recover_from_error(self) -> bool:
        """Run the documented recovery sequence for error state 200.

        The specification requires 0x0D00, 0x0D05 and 0x0302 to be written,
        in that order. Reassert the existing commands: recovering communication
        is not authorization to start charging. The >=6 A example in the
        specification starts a charge; it must not replace a pause or zero.
        """

        self._require_write_access()
        async with self._current_lock:
            try:
                error = await self._client.async_read_register(ERROR_CODE)
                if error == 0:
                    return False
                if error != ERROR_CODE_EMS_UNAVAILABLE:
                    raise validation_error("recovery_not_applicable")
                current = await self._client.async_read_register(CHARGING_CURRENT_EMS)
            except AmtronBusError as err:
                raise validation_error("recovery_state_unknown") from err
            if not isinstance(current, (int, float)) or not math.isfinite(current) or current < 0:
                raise validation_error("recovery_state_unknown")
            # Recovery reasserts existing intent, including a deliberate
            # pause. It never starts the long state-change timer again.
            try:
                self._client.check_write_interval(
                    CHARGING_CURRENT_EMS, min_interval=MIN_CURRENT_WRITE_INTERVAL_SECONDS
                )
            except AmtronRateLimitedError as err:
                self._diagnostics.writes_rate_limited += 1
                self._diagnostics.timeline.record(
                    EventType.WRITE_RATE_LIMITED, register=CHARGING_CURRENT_EMS.address
                )
                raise validation_error(
                    "write_rate_limited", {"seconds": f"{err.retry_after:.0f}"}
                ) from err
            remembered = self.charging_setpoint
            paused = (
                self.energy_goal_stop_requested or self.pause_requested is True
                or 0 < current < CHARGING_CURRENT_MINIMUM
            )
            if paused:
                target = CHARGING_PAUSE_CURRENT
            elif current == CHARGING_CURRENT_UNLIMITED:
                target = CHARGING_CURRENT_UNLIMITED
            else:
                target = (
                    min(current, remembered)
                    if remembered is not None and remembered >= CHARGING_CURRENT_MINIMUM
                    else current
                )
            self._diagnostics.timeline.record(EventType.RECOVERY_STARTED)
            try:
                await self._async_write(HEARTBEAT, HEARTBEAT_VALUE)
                # Error 200 requires release before the current register. The
                # current step below reasserts a goal stop as the pause value;
                # rejecting this recovery-only release would leave it faulted.
                await self._async_write(
                    CHARGING_RELEASE, int(self.charging_release_requested is not False),
                    recovery_release=True,
                )
                await self._async_apply_current(
                    min(target, self.max_charging_current),
                    preserve_setpoint=paused,
                    recovery=True,
                )
            except (DeviceError, AmtronBusError, OSError, RuntimeError, ValueError, asyncio.CancelledError) as err:
                self._recovery_deadline = None
                self.events.record(
                    "uncertain" if self._uncertain(err) else "recovery_failed", "recover",
                    "connection_error" if self._uncertain(err) else "write_rejected",
                )
                raise
        self._diagnostics.recoveries += 1
        self._diagnostics.timeline.record(EventType.RECOVERY_COMPLETED)
        self._recovery_generation = self._write_state.current_write_generation
        self._recovery_deadline = self._monotonic() + 30
        return True

    async def async_restart(self) -> None:
        """Restart the wallbox through 0x0D19, which needs the idle state."""

        self._require_write_access()
        async with self._current_lock:
            state = await self._async_current_evse_state("restart_state_unknown")
            if state != EvseState.IDLE:
                raise validation_error("restart_requires_idle")
            await self._async_write(SYSTEM_RESTART, SYSTEM_RESTART_VALUE)

    async def _async_current_evse_state(self, error_key: str) -> EvseState:
        """Confirm the device state immediately before a guarded operation."""

        try:
            state = await self._client.async_read_register(EVSE_STATE)
        except AmtronBusError as err:
            raise validation_error(error_key) from err
        if not isinstance(state, int) or state not in EvseState:
            raise validation_error(error_key)
        return EvseState(state)

    async def async_shutdown(self) -> None:
        """Cancel deferred work; a new owner never inherits a delayed start."""

        self._clear_pending(outcome="cancelled", reason="shutdown")
        task = self._flush_task
        self._flush_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    @staticmethod
    def _uncertain(error: BaseException) -> bool:
        return not isinstance(error, DeviceValidationError) and not isinstance(
            error.__cause__,
            (AmtronWriteRejectedError, AmtronIllegalAddressError, AmtronIdentityError),
        )

    def _current_transition(self, value: float) -> bool:
        paused = self.is_paused
        if self.pause_requested is True:
            paused = True
        target_paused = 0 < value < CHARGING_CURRENT_MINIMUM
        return paused is None or paused != target_paused

    async def _async_vehicle_absent(self) -> bool:
        """Grant an exception only from fresh, consistent EVSE and CP readings."""
        if self._identity.layout_version < CP_STATE.min_layout:
            return False
        try:
            state = await self._client.async_read_register(EVSE_STATE)
            if type(state) is not int or state != EvseState.IDLE:
                return False
            cp = await self._client.async_read_register(CP_STATE)
        except AmtronBusError:
            return False
        return type(cp) is int and cp in (CpState.A1, CpState.A2)

    async def _async_check_transition_interval(self) -> None:
        remaining = self._mode_change_remaining()
        if remaining > 0 and await self._async_vehicle_absent():
            return
        remaining = self._mode_change_remaining()
        if remaining > 0:
            raise AmtronRateLimitedError("state_change", remaining)

    def _mode_change_remaining(self) -> float:
        if self._write_state.mode_change_at is None:
            return 0.0
        waited = self._monotonic() - self._write_state.mode_change_at
        return max(0.0, self._options.state_change_interval_seconds - waited)

    async def _async_apply_current(
        self, value: float, *, preserve_setpoint: bool = False, recovery: bool = False,
        phase_preparation: bool = False, energy_goal_stop: bool = False,
    ) -> None:
        transition = not recovery and (
            self._current_transition(value)
            or (energy_goal_stop and self.is_paused is not True)
        )
        paused = 0 < value < CHARGING_CURRENT_MINIMUM
        previous = self._write_state.confirmed_current
        if transition and not phase_preparation and not energy_goal_stop:
            await self._async_check_transition_interval()
        if not paused and self.energy_goal_stop_requested:
            raise validation_error("energy_goal_stop_active")
        try:
            await self._async_write(
                CHARGING_CURRENT_EMS, value,
                min_interval=MIN_CURRENT_WRITE_INTERVAL_SECONDS, refresh=False,
            )
        except (DeviceError, asyncio.CancelledError) as err:
            if self._uncertain(err):
                self._clear_pending()
                self._write_state.confirmed_current = None
                if not paused:
                    self.charging_setpoint = value
                if transition:
                    self._write_state.mode_change_at = self._monotonic()
                if paused:
                    self.pause_requested = True
                    self._remember_pause_setpoint(previous, preserve_setpoint)
            raise
        self._write_state.confirmed_current = value
        self.pause_requested = paused
        if not paused:
            self.charging_setpoint = value
        else:
            self._remember_pause_setpoint(previous, preserve_setpoint)
        if transition:
            self._write_state.mode_change_at = self._monotonic()
        if not recovery and not phase_preparation:
            # The command reached the device. A failed visibility/persistence
            # callback must never allow an older queued command to run later.
            self._clear_pending()
        if self._request_refresh is not None:
            await self._request_refresh()

    def _remember_pause_setpoint(
        self, previous: float | None, preserve_setpoint: bool
    ) -> None:
        """Retain the confirmed current even when the pause acknowledgement is lost."""

        if (
            (not preserve_setpoint or self.charging_setpoint is None)
            and previous is not None
            and math.isfinite(previous)
            and (previous == CHARGING_CURRENT_UNLIMITED or previous >= CHARGING_CURRENT_MINIMUM)
        ):
            self.charging_setpoint = float(previous)

    async def _async_read_switch(self, spec: RegisterSpec, *, stopping: bool = False) -> int | None:
        """Recheck live state, but a failed read must not prevent a stop."""
        try:
            value = await self._client.async_read_register(spec)
        except AmtronBusError as err:
            if stopping:
                return None
            raise validation_error("control_state_unknown") from err
        if type(value) is not int or value not in (0, 1):
            if stopping:
                return None
            raise validation_error("control_state_unknown")
        return value

    async def _async_transition_write(
        self, spec: RegisterSpec, value: int, *, changed: bool,
        min_interval: float = 0.0,
    ) -> None:
        if self.energy_goal_stop_requested and (
            (spec == CHARGING_RELEASE and value == 1)
            or (spec == LOCK_EVSE and value == 0)
        ):
            raise validation_error("energy_goal_stop_active")
        try:
            await self._async_write(spec, value, min_interval=min_interval, refresh=False)
        except (DeviceError, asyncio.CancelledError) as err:
            if changed and self._uncertain(err):
                self._write_state.mode_change_at = self._monotonic()
            raise
        if changed:
            self._write_state.mode_change_at = self._monotonic()
        self._clear_pending()
        if self._request_refresh is not None:
            await self._request_refresh()

    async def _async_apply_release(self, released: bool) -> None:
        current = await self._async_read_switch(CHARGING_RELEASE, stopping=not released)
        changed = current != int(released)
        if changed:
            await self._async_check_transition_interval()
        previous = self.charging_release_requested
        self.charging_release_requested = released
        try:
            await self._async_transition_write(CHARGING_RELEASE, int(released), changed=changed)
        except (DeviceError, asyncio.CancelledError) as err:
            # A failed block retains its intent unless identity verification
            # proves that no command could have reached the intended device.
            if released or isinstance(err.__cause__, AmtronIdentityError):
                self.charging_release_requested = previous
            raise

    async def _async_apply_lock(self, locked: bool) -> None:
        current = await self._async_read_switch(LOCK_EVSE, stopping=locked)
        changed = current != int(locked)
        if changed:
            await self._async_check_transition_interval()
        await self._async_transition_write(LOCK_EVSE, int(locked), changed=changed)

    async def _async_apply_phases(self, phases: int) -> None:
        current = await self._async_read_switch(REQUESTED_PHASES)
        if current == phases:
            return
        change = self._phase_change
        if change is None:
            # Admit the whole pause/phase operation once. Its own pause must
            # not trigger another long wait within this operation.
            await self._async_check_transition_interval()
            self._client.check_write_interval(
                REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS
            )
            state = await self._async_current_evse_state("phase_state_unknown")
            if state not in (EvseState.IDLE, EvseState.EV_CONNECTED,
                             EvseState.PRECONDITIONS_VALID, EvseState.READY_TO_CHARGE,
                             EvseState.CHARGING):
                raise validation_error("phase_pause_state_unknown")
            if (
                state == EvseState.IDLE
                and self._identity.layout_version >= CP_STATE.min_layout
                and not await self._async_vehicle_absent()
            ):
                raise validation_error("phase_pause_state_unknown")
            if state != EvseState.IDLE:
                # Preparation physically pauses charging. Discard the older
                # command before that write, including if persistence fails.
                self._clear_pending()
                await self._async_apply_current(CHARGING_PAUSE_CURRENT, phase_preparation=True)
                change = _PhaseChange(self._monotonic() + _PHASE_PAUSE_TIMEOUT)
                self._phase_change = change
        if change is not None:
            await self._async_confirm_phase_pause(change)
        # Every resumed step rechecks live state and hardware identity through
        # the client; no register lock is held during the waiting periods.
        self._client.check_write_interval(
            REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS
        )
        await self._async_transition_write(
            REQUESTED_PHASES, phases, changed=True,
            min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS,
        )

    async def _async_confirm_phase_pause(self, change: _PhaseChange) -> None:
        state = await self._async_current_evse_state("phase_pause_state_unknown")
        if state in (EvseState.NOT_INITIALIZED, EvseState.ERROR, EvseState.SERVICE_MODE):
            raise validation_error("phase_pause_state_unknown")
        paused = state in (EvseState.IDLE, EvseState.EV_CONNECTED)
        if self._identity.layout_version >= CP_STATE.min_layout:
            try:
                cp = await self._client.async_read_register(CP_STATE)
            except AmtronBusError as err:
                raise validation_error("phase_pause_state_unknown") from err
            if type(cp) is not int or cp not in CpState or cp in (CpState.INIT, CpState.E, CpState.F):
                raise validation_error("phase_pause_state_unknown")
            paused = paused and (
                cp in (CpState.A1, CpState.A2) if state == EvseState.IDLE else cp == CpState.B1
            )
        now = self._monotonic()
        if paused and state == EvseState.IDLE:
            # Unplugging also ends an already pending vehicle grace period.
            change.ready_at = now
            return
        if not paused:
            if change.ready_at is not None:
                raise validation_error("phase_pause_state_unknown")
            if now >= change.confirmation_deadline:
                raise validation_error("phase_pause_timeout")
            raise _PhaseWaitError(min(1.0, change.confirmation_deadline - now), preserve_deadline=True)
        if change.ready_at is None:
            change.ready_at = now + self._options.phase_switch_grace_seconds
        remaining = change.ready_at - now
        if remaining > 0:
            raise _PhaseWaitError(remaining)

    async def _async_apply_command(self, command: _PendingCommand) -> None:
        self._require_write_access()
        if (
            command.recovery_generation is not None
            and command.recovery_generation != self._write_state.energy_goal_generation
        ):
            raise validation_error("energy_goal_stop_active")
        if self.energy_goal_stop_requested and command.action in (
            "set_current", "resume", "enable_charging", "unlock",
        ):
            raise validation_error("energy_goal_stop_active")
        if command.action == "pause":
            await self._async_apply_current(
                CHARGING_PAUSE_CURRENT, energy_goal_stop=command.energy_goal_stop,
            )
        elif command.action in ("set_current", "resume"):
            # A configuration change can lower the available current while
            # an accepted request is waiting. Reapply the active limit.
            await self._async_apply_current(min(command.value, self.max_charging_current))
        elif command.action in ("single_phase", "all_phases"):
            if not phase_switch_is_supported(self._identity.phase_options_hw):
                raise validation_error("phase_switch_unsupported")
            await self._async_apply_phases(int(command.value))
        elif command.action in ("enable_charging", "disable_charging"):
            await self._async_apply_release(bool(command.value))
        else:
            await self._async_apply_lock(bool(command.value))

    async def _async_observe_command(self, command: _PendingCommand, *, deferred: bool = False) -> None:
        """Report the bus outcome independently of status/persistence callbacks."""
        self._active_command = command
        self._command_acknowledged = False
        self._command_write_started = False
        try:
            self._check_timed_command()
            if deferred and self._validate_deferred is not None:
                self._validate_deferred()
            await self._async_apply_command(command)
        except _TimedCommandCancelledError as err:
            self.events.record("cancelled", command.action, err.reason)
        except (_PhaseWaitError, AmtronRateLimitedError):
            raise
        except (DeviceError, AmtronBusError, OSError, RuntimeError, ValueError, asyncio.CancelledError) as err:
            key = err.translation_key if isinstance(err, DeviceError) else ""
            reason = {
                "phase_pause_timeout": "phase_timeout",
                "phase_pause_state_unknown": "phase_unconfirmed",
                "phase_state_unknown": "phase_unconfirmed",
                "read_only_mode": "permission_denied",
                "energy_goal_stop_active": "energy_goal",
            }.get(key, "write_rejected" if not self._uncertain(err) else "connection_error")
            if self._command_acknowledged:
                self.events.record("executed", command.action, "status_refresh_failed")
            else:
                uncertain = self._command_write_started and self._uncertain(err)
                self.events.record("uncertain" if uncertain else "failed", command.action, reason)
            raise
        else:
            self._record_replaced(command)
            self.events.record("executed", command.action)
        finally:
            self._active_command = None

    async def _async_submit_locked(self, command: _PendingCommand, *, automatic: bool = False) -> None:
        """Accept the latest request, coalescing all deferred commands together."""
        if not automatic:
            self._write_state.command_intent_generation += 1
            if self.goal_controller is not None:
                await self.goal_controller.async_cancel_recovery_locked()
        if self._phase_change is not None and command != self._pending:
            self._record_replaced(command)
            await self._async_cancel_pending()
        had_pending = self._pending is not None
        try:
            await self._async_observe_command(command)
        except _PhaseWaitError as err:
            await self._async_queue(command, err.retry_after, preserve_deadline=err.preserve_deadline)
            return
        except AmtronRateLimitedError as err:
            self._diagnostics.writes_rate_limited += 1
            self._diagnostics.timeline.record(EventType.WRITE_RATE_LIMITED)
            if command.action in ("set_current", "resume") and command.owner is None:
                self.charging_setpoint = command.value
            await self._async_queue(command, err.retry_after)
            return
        except (DeviceError, asyncio.CancelledError) as err:
            if self._phase_change is not None or self._uncertain(err):
                self._clear_pending()
                if had_pending:
                    await self._async_publish_pending()
            raise
        await self._async_cancel_pending()

    async def _async_queue(
        self, command: _PendingCommand, delay: float, *, preserve_deadline: bool = False,
    ) -> None:
        due = self._monotonic() + delay
        if preserve_deadline and self._pending == command:
            self._pending_due = due
            return
        if self._pending == command and abs(self._pending_due - due) < 0.01:
            return
        self._record_replaced(command)
        self._pending = command
        self._pending_due = due
        self._pending_until = (
            None if self._phase_change is not None and self._phase_change.ready_at is None
            else datetime.now(UTC) + timedelta(seconds=delay)
        )
        self._pending_changed.set()
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(
                self._async_flush_pending(), name="mennekes_amtron_rtu_control_flush"
            )
        await self._async_publish_pending()

    def _record_replaced(self, command: _PendingCommand | None) -> None:
        if self._pending is not None and self._pending != command:
            self.events.record("replaced", self._pending.action, "replaced")

    def _clear_pending(self, *, outcome: str | None = None, reason: str = "completed") -> bool:
        changed = self._pending is not None
        if self._pending is not None:
            if outcome is not None and self._active_command is None:
                self.events.record(outcome, self._pending.action, reason)
            elif self._active_command is not None:
                self._record_replaced(self._active_command)
        self._pending = None
        self._phase_change = None
        self._pending_until = None
        self._pending_changed.set()
        return changed

    async def _async_publish_pending(self) -> None:
        if self._request_refresh is not None:
            try:
                await self._request_refresh()
            except (AmtronBusError, OSError):
                # A subsequent normal poll publishes the cleared/queued
                # status. Do not retry a command because visibility failed.
                _LOGGER.debug("Unable to refresh queued command status")

    async def _async_cancel_pending(self) -> None:
        if self._clear_pending():
            await self._async_publish_pending()

    async def _async_wait_pending(self, delay: float) -> None:
        sleeping = asyncio.ensure_future(self._sleep(delay))
        changed = asyncio.create_task(self._pending_changed.wait())
        try:
            await asyncio.wait((sleeping, changed), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeping, changed):
                task.cancel()
            await asyncio.gather(sleeping, changed, return_exceptions=True)

    async def _async_flush_pending(self) -> None:
        try:
            while True:
                async with self._current_lock:
                    command = self._pending
                    if command is None:
                        return
                    if reason := self._timed_cancellation_reason(command):
                        self._clear_pending(outcome="cancelled", reason=reason)
                        await self._async_publish_pending()
                        return
                    delay = self._pending_due - self._monotonic()
                    check_disconnect = (
                        self._phase_change.ready_at is not None
                        if self._phase_change is not None
                        else self._mode_change_remaining() > 0
                    )
                    if delay > 0 and check_disconnect and await self._async_vehicle_absent():
                        delay = 0
                    if delay <= 0:
                        try:
                            await self._async_observe_command(command, deferred=True)
                        except _PhaseWaitError as err:
                            await self._async_queue(
                                command, err.retry_after, preserve_deadline=err.preserve_deadline,
                            )
                            delay = err.retry_after
                        except AmtronRateLimitedError as err:
                            await self._async_queue(command, err.retry_after)
                            delay = err.retry_after
                        except (DeviceError, AmtronBusError, OSError) as err:
                            # One attempt only. A lost acknowledgement must
                            # never produce an automatic duplicate start.
                            _LOGGER.warning(
                                "Queued %s command cancelled (%s)", command.action,
                                err.translation_key if isinstance(err, DeviceError)
                                else type(err).__name__,
                            )
                            self._clear_pending()
                            await self._async_publish_pending()
                            return
                        else:
                            await self._async_cancel_pending()
                            return
                    self._pending_changed.clear()
                # Recheck unplugging without publishing a changing countdown.
                await self._async_wait_pending(min(delay, MIN_CURRENT_WRITE_INTERVAL_SECONDS))
        except (OSError, RuntimeError, ValueError) as err:
            # A failing refresh/persistence callback must not leave an
            # unobserved task or a queued start with no owner. Never retry it.
            self._clear_pending()
            _LOGGER.warning("Deferred command cancelled after %s", type(err).__name__)
        finally:
            self._flush_task = None

    async def _async_write(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
        refresh: bool = True,
        recovery_release: bool = False,
    ) -> None:
        self._check_timed_command()
        if self._active_command is not None:
            self._command_write_started = True
        try:
            if self._write_enables_charging(spec, value) or (
                self._active_command is not None and self._active_command.deadline is not None
            ):
                await self._client.async_write_register(
                    spec, value, min_interval=min_interval,
                    before_write=lambda: self._check_write_allowed(
                        spec, value, recovery_release=recovery_release,
                    ),
                )
            else:
                await self._client.async_write_register(
                    spec, value, min_interval=min_interval
                )
        except AmtronRateLimitedError:
            self._diagnostics.timeline.record(EventType.WRITE_RATE_LIMITED, register=spec.address)
            raise
        except AmtronBusError as err:
            if isinstance(err, AmtronIdentityError):
                raise device_error("device_identity_changed") from err
            self._diagnostics.timeline.record(EventType.WRITE_REJECTED, register=spec.address)
            self._diagnostics.writes_rejected += 1
            if spec != HEARTBEAT:
                self._diagnostics.write_errors.add(spec.key)
            self._diagnostics.last_error = str(err)
            if self._notify_write_error is not None:
                self._notify_write_error()
            raise device_error(
                "write_failed", {"register": spec.key, "error": str(err)}
            ) from err
        self._diagnostics.writes_sent += 1
        if self._active_command is not None:
            action = self._active_command.action
            expected = (
                CHARGING_CURRENT_EMS if action in ("pause", "resume", "set_current")
                else REQUESTED_PHASES if action in ("single_phase", "all_phases")
                else CHARGING_RELEASE if action in ("enable_charging", "disable_charging")
                else LOCK_EVSE
            )
            if spec == expected:
                self._command_acknowledged = True
        if spec.key in self._diagnostics.write_errors:
            self._diagnostics.write_errors.remove(spec.key)
            if self._notify_write_error is not None:
                self._notify_write_error()
        if refresh and self._request_refresh is not None:
            await self._request_refresh()

    @staticmethod
    def _write_enables_charging(spec: RegisterSpec, value: float) -> bool:
        return (
            (spec == CHARGING_CURRENT_EMS and (value == 0 or value >= CHARGING_CURRENT_MINIMUM))
            or (spec == CHARGING_RELEASE and value == 1)
            or (spec == LOCK_EVSE and value == 0)
        )

    def _check_write_allowed(
        self, spec: RegisterSpec, value: float, *, recovery_release: bool,
    ) -> None:
        # Observations can latch a stop while this command awaits the bus or
        # physical identity. Recheck under the bus lock, immediately before
        # sending, so a refused start neither writes nor consumes the 5s floor.
        self._check_timed_command()
        if (
            self._write_enables_charging(spec, value) and self.energy_goal_stop_requested
            and not (recovery_release and spec == CHARGING_RELEASE)
        ):
            raise validation_error("energy_goal_stop_active")

    def _timed_cancellation_reason(self, command: _PendingCommand) -> str | None:
        if command.owner is None:
            return None
        if command.deadline is not None and datetime.now(UTC) >= command.deadline:
            return "deadline_expired"
        if self.soc_goal.status != "active" or self.soc_goal.target_percent <= 0 or self.energy_goal_stop_requested:
            return "energy_goal"
        return None

    def _check_timed_command(self) -> None:
        command = self._active_command
        if command is not None and (reason := self._timed_cancellation_reason(command)):
            raise _TimedCommandCancelledError(reason)

    def _require_write_access(self) -> None:
        if self._options.read_only:
            raise validation_error("read_only_mode")
