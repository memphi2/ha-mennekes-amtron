"""The commands of the wallbox control.

The write path itself is in ``control_write.py``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .capabilities import phase_switch_is_supported
from .client import LockedRegisterReader, MennekesModbusClient
from .client_errors import (
    AmtronBusError,
    AmtronIdentityError,
    AmtronIllegalAddressError,
    AmtronRateLimitedError,
    AmtronWriteRejectedError,
)
from .command_events import CONTROL_ACTIONS, CommandEvents, command_source
from .const import (
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_UNLIMITED,
    CHARGING_PAUSE_CURRENT,
    ERROR_CODE_EMS_UNAVAILABLE,
    HEARTBEAT_VALUE,
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
    SYSTEM_RESTART_VALUE,
)
from .control_queue import CommandQueue
from .control_types import (
    STOPS,
    ControlOptions,
    DepartureCommandCancelledError,
    PendingCommand,
    PhaseChange,
    pause_state,
)
from .control_write import WritePath
from .data import DeviceIdentity, WallboxData, WriteDiagnostics
from .energy_goal import EnergyGoal
from .enums import EvseState, RequestedPhases, SolarChargingMode
from .errors import DeviceError, DeviceValidationError, validation_error
from .plug_charge import PlugChargeController
from .plug_cycle import PlugChange
from .registers import (
    CHARGING_CURRENT_EMS,
    CHARGING_RELEASE,
    ENERGY_TOTAL,
    ERROR_CODE,
    HEARTBEAT,
    SOLAR_CHARGING_MODE,
    SYSTEM_RESTART,
)
from .runtime_events import EventType
from .soc_goal import SocGoal
from .unplug_release import UnplugRelease
from .write_state import WriteState

if TYPE_CHECKING:
    from .energy_goal_controller import EnergyGoalController
    from .soc_goal_controller import SocGoalController


__all__ = ["PENDING_ACTIONS", "AmtronControl", "ControlOptions"]

_LOGGER = logging.getLogger(__name__)
PENDING_ACTIONS = CONTROL_ACTIONS
# How long a read-back goal pause is trusted before it is read again.
_GOAL_PAUSE_RECHECK_SECONDS = 60.0
# A float32 read-back of the cap itself is not above it.
_CAP_TOLERANCE_A = 0.05


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
        self._validate_deferred = validate_deferred
        self._sleep = sleep
        self._monotonic = monotonic
        self._pending: PendingCommand | None = None
        self._phase_change: PhaseChange | None = None
        self._release_writes = 0
        # When, and on which connection, a goal pause was last read back.
        self._goal_pause_confirmed: tuple[float, int] | None = None
        # Whose pause is in place: "manual", "soc_stop" for the charge
        # target's stop, or the source that wrote it. Not kept over a
        # restart: a pause of unknown owner counts as anybody's and stays.
        self.pause_owner: str | None = None
        self._pending_due = 0.0
        self._pending_until: datetime | None = None
        self._pending_changed = asyncio.Event()
        self._current_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._write_state = client.write_state
        self._events = CommandEvents()
        self._active_command: PendingCommand | None = None
        # Set by the queue while a command runs that must not settle a waiting stop.
        self._keep_pending = False
        self._command_acknowledged = False
        self._guest_release_applied = False
        self._command_write_started = False
        self._recovery_deadline: float | None = None
        self._recovery_generation = 0
        self.goal_controller: EnergyGoalController | None = None
        self.soc_goal_controller: SocGoalController | None = None
        self._plug_charge = PlugChargeController(self)
        self.unplug_release = UnplugRelease(self)
        # The parts hold no state of their own; they work on this control.
        self.writes = WritePath(self)
        self.queue = CommandQueue(self)
        self.guest_confirmation_required: Callable[[], bool] = lambda: False
        self.time_limit_reached: Callable[[], bool] = lambda: False
        # Whether a guest charges now, as guest charging says.
        self.guest_mode: Callable[[], bool] = lambda: False

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
            else (not self.guest_mode() and self.soc_goal.stop_requested)
        )

    @property
    def energy_goal_stop_requested(self) -> bool:
        """Every stop that fences starts: energy, vehicle percentage and guest time."""
        return self.goal_stop_requested or self.time_limit_reached() or self.guest_confirmation_required()

    @property
    def guest_release_applied(self) -> bool:
        """Whether the guest start in progress has already opened the release."""
        return self._guest_release_applied

    @property
    def foreign_stop_requested(self) -> bool:
        """A stop that is not the energy goal's own: percentage or guest."""
        return self._soc_stop_requested or self.time_limit_reached() or self.guest_confirmation_required()

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
        """Return the remembered resume target, kept across reconnects and restarts."""

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
        service reports, so a user never sees a value the wallbox would
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
    def _validate_departure_owner(owner: str) -> None:
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise ValueError("invalid departure command owner")

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

    def _record_replaced(self, command: PendingCommand | None) -> None:
        if self._pending is not None and self._pending != command and not self._keep_pending:
            self.events.record("replaced", self._pending.action, "replaced", source=self._pending.source, requested_value=self._pending.value)

    def _clear_pending(self, *, outcome: str | None = None, reason: str = "completed") -> bool:
        if self._keep_pending and outcome is None:
            return False
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

    def _command_cancellation_reason(self, command: PendingCommand) -> str | None:
        if command.phase_scope is not None:
            cycle = self.write_state.plug_cycle
            if (
                cycle.present is not True or cycle.scope != command.phase_scope
                or self.charging_release_requested is False or self.pause_owner != "phase_switch"
            ):
                return "unknown"
            gate = self._plug_charge.state
            if gate.enabled and not (gate.manual or gate.authorized):
                return "permission_denied"
        if command.grant_release and not self.guest_mode():
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
            raise DepartureCommandCancelledError(reason)

    def _require_write_access(self) -> None:
        if self.read_only:
            raise validation_error("read_only_mode")

    @property
    def plug_charge(self) -> dict[str, object]:
        return {**self._plug_charge.state.snapshot(self.write_state.plug_cycle), "manual": self._plug_charge.state.manual}

    def observe_plug(self, data: WallboxData | None, *, observed_at: datetime | None = None) -> None:
        """Take one poll, or None for a failed one, into the plug cycle."""
        self.follow_plug(
            self.write_state.plug_cycle.read(data, self.layout_version, at=observed_at, now=self.monotonic()), data,
        )

    def follow_plug(self, change: PlugChange, data: WallboxData | None) -> None:
        """Tell the rules that depend on the connection what the plug cycle took in."""
        self._plug_charge.follow(change, data)
        self.unplug_release.follow()

    async def async_sync_plug_charge(self) -> None:
        await self._plug_charge.async_sync()

    async def async_configure_plug_charge(self, enabled: bool, *, policy: str = "") -> None:
        await self._plug_charge.async_configure(enabled, policy)

    async def async_authorize_plug_charge(
        self, session: str, *, deadline: datetime, end_pause: bool = False,
    ) -> None:
        """Grant the plug-in; with ``end_pause`` a manual pause ends after the grant.

        The grant comes first, written at the pause current, so it starts no
        charge: a grant that fails leaves the pause as it was. A command
        that waits is newer intent and keeps the pause too.
        """
        await self._plug_charge.async_authorize(session, deadline)
        if (
            end_pause and self.pause_requested is True and self._pending is None
            and self.pause_owner == "manual" and self.is_paused is True
        ):
            await self.async_resume_charging()

    async def async_set_energy_goal(self, value: float) -> None:
        """Change the energy ceiling without starting or resuming charging."""
        if self.goal_controller is None or not self.supports_energy_goal:
            raise validation_error("energy_goal_not_supported")
        await self.goal_controller.async_set_target(value)

    async def async_pause_for_energy_goal(self) -> bool:
        """Enforce a latched energy stop, retaining only the current-write floor.

        Returns whether it may have changed what is saved: a stop that holds
        and was read back a moment ago changes nothing.
        """
        async with self._current_lock:
            if not self.energy_goal_stop_requested:
                return False
            changed = False
            if self.foreign_stop_requested and self.goal_controller is not None:
                # Another limit stops the charge. A pause for a missing meter
                # is no longer the energy goal's to end: lifting this limit
                # later must not start a charge.
                changed = self.goal_controller.drop_recovery()
            self._require_write_access()
            confirmed = self._goal_pause_confirmed
            polled = self._state().fresh(CHARGING_CURRENT_EMS.key)
            if (
                # A waiting stop stays queued anyway and changes nothing here.
                confirmed is not None and (self._pending is None or self._pending.action in STOPS)
                and confirmed[1] == self._client.connection_generation
                and 0 <= self._monotonic() - confirmed[0] < _GOAL_PAUSE_RECHECK_SECONDS
                and isinstance(polled, (int, float)) and not isinstance(polled, bool)
                and 0 < polled < CHARGING_CURRENT_MINIMUM
            ):
                # A latched stop stays for hours. The pause was read back a
                # moment ago on this connection, and this poll still reads it;
                # a reboot or fallback shows up in the poll or the reconnect.
                return changed
            if await self.writes.async_vehicle_absent():
                # Nothing charges without a car; a later poll pauses once one
                # is plugged in.
                return changed
            if self._pending is not None and self._pending.action not in STOPS:
                # The stop replaces a start that waited for the current. A
                # waiting stop stays, a pause included: this stop's latch may
                # be lifted before its own pause is written.
                self._clear_pending(outcome="cancelled", reason=self._stop_reason())
            if self.is_paused is not False:
                try:
                    # A former acknowledgement or cached poll is not proof of
                    # the present register value. In particular, a poll can
                    # detect a reached goal while its current block has failed
                    # and the coordinator still exposes the previous snapshot.
                    # A reconnect still triggers the client's identity check.
                    # With an established connection, avoid holding the bus
                    # for two retrying reads before a priority heartbeat.
                    current = await self._client.async_read_register(CHARGING_CURRENT_EMS)
                except AmtronBusError:
                    current = None
                if (
                    not isinstance(current, bool) and isinstance(current, (int, float))
                    and math.isfinite(current) and current >= 0
                ):
                    self._write_state.confirmed_current = float(current)
                    if 0 < current < CHARGING_CURRENT_MINIMUM:
                        self.pause_requested = True
                        self._goal_pause_confirmed = (
                            self._monotonic(), self._client.connection_generation,
                        )
                        return True
                else:
                    self._write_state.confirmed_current = None
            reason = self._stop_reason()
            source = (
                "guest" if reason in ("time_limit", "guest_confirmation") else
                "soc_goal" if self._soc_stop_requested else "energy_goal"
            )
            await self.queue.async_observe_command(PendingCommand(
                "pause", CHARGING_PAUSE_CURRENT, energy_goal_stop=True,
                source=source, completion_reason=reason,
            ))
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

    def cancel_goal_resume(self, *, replaced: bool = False) -> None:
        """Invalidate automatic requests calculated for a previous goal.

        ``replaced``: a command by hand takes the queued one's place. A
        planner's waiting stop is not a request of that kind: whether the
        command overrides it is the queue's stop rule.
        """
        if self._pending is not None and (
            self._pending.recovery_generation is not None or self._pending.owner is not None
        ) and not (replaced and self._pending.action in STOPS and self._pending.recovery_generation is None):
            self._clear_pending(
                outcome="replaced" if replaced else "cancelled",
                reason="replaced" if replaced else "energy_goal",
            )

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
        await self.queue.async_submit_locked(
            PendingCommand("resume", target, recovery_generation=generation, source="energy_goal"), automatic=True,
        )

    def observe_recovery(self, error_code: object, current_write_generation: int) -> None:
        """Confirm a completed recovery from a subsequent successful fresh poll."""
        if (
            self._recovery_deadline is None or type(error_code) is not int
            or current_write_generation < self._recovery_generation
        ):
            return
        if error_code == 0:
            self.events.record("recovery_succeeded", "recover", source="recovery")
        elif self._monotonic() >= self._recovery_deadline:
            self.events.record("recovery_failed", "recover", "error_still_active", source="recovery")
        else:
            return
        self._recovery_deadline = None

    async def async_cancel_pending_change(self) -> None:
        """Discard future work, retaining any pause or setpoint already applied."""
        async with self._current_lock:
            self._require_write_access()
            self._clear_pending(outcome="cancelled", reason="user_cancelled")

    async def async_set_limits(self, options: ControlOptions) -> None:
        """Use new limits without a restart; read-only mode still needs one.

        New commands use them at once. A queued start or stop is timed again
        with the new state-change interval; a phase-switch wait that already
        runs keeps its length.
        """
        if options.read_only != self._options.read_only:
            raise ValueError("read_only needs a restart")
        async with self._current_lock:
            retime = (
                options.state_change_interval_seconds != self._options.state_change_interval_seconds
                and self._pending is not None and self._phase_change is None
            )
            self._options = options
            if retime:
                # The retry recomputes the wait or runs; a write it cannot
                # make yet raises before anything reaches the bus.
                self._pending_due = self._monotonic()
                self._pending_changed.set()

    async def async_enforce_current_cap(self) -> bool:
        """Lower a freshly read charging current above a lowered cap.

        A queued command applies the cap when it runs; a pause and 0 A stay
        as they are.
        """
        if self.read_only:
            return False
        async with self._current_lock:
            snapshot = self._state()
            current = (
                snapshot.fresh(CHARGING_CURRENT_EMS.key)
                if snapshot.current_write_generation == self._write_state.current_write_generation
                else None
            )
            maximum = self.max_charging_current
            if (
                self._pending is not None or self._phase_change is not None
                or self._active_command is not None or self.energy_goal_stop_requested
                or isinstance(current, bool) or not isinstance(current, (int, float))
                or not math.isfinite(current) or current < CHARGING_CURRENT_MINIMUM
                or current <= maximum + _CAP_TOLERANCE_A
            ):
                return False
            await self.queue.async_submit_locked(PendingCommand("set_current", maximum, keeps_setpoint=True))
            return True

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
            _LOGGER.info("Control: disabling the EMS current limit (0 A)")
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
            await self.queue.async_submit_locked(PendingCommand("set_current", current))

    async def async_pause_charging(self) -> None:
        """Pause charging the documented way, by signalling 0 A to the EV."""

        self._require_write_access()
        async with self._current_lock:
            await self.queue.async_submit_locked(PendingCommand("pause", CHARGING_PAUSE_CURRENT))

    async def async_set_departure_current(
        self, value: float | None, *, deadline: datetime, owner: str,
    ) -> None:
        """Apply a trip's current without leaving an unbounded queued command."""
        self._require_write_access()
        self._validate_departure_owner(owner)
        if not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None:
            raise ValueError("departure command deadline needs a timezone")
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < CHARGING_CURRENT_MINIMUM
        ):
            raise validation_error("charging_current_invalid", {"value": str(value), "minimum": "6"})
        command = PendingCommand(
            "pause" if value is None else "set_current",
            CHARGING_PAUSE_CURRENT if value is None else min(value, self.max_charging_current),
            owner=owner, deadline=deadline, source="departure_plan",
        )
        async with self._current_lock:
            # A manual request can arrive while the caller waits for this lock.
            if self._pending is not None and self._pending.owner != owner:
                return
            if reason := self._command_cancellation_reason(command):
                # Refused plans must not change remembered current or command
                # intent, or cancel another goal's automatic recovery.
                self.events.record("cancelled", command.action, reason, source=command.source, requested_value=command.value)
                await self.queue.async_cancel_pending()
                return
            await self.queue.async_submit_locked(command)

    async def async_cancel_departure_current(self, owner: str) -> None:
        """Cancel only this planner's queue, even when writes became disabled."""
        self._validate_departure_owner(owner)
        async with self._current_lock:
            if self._pending is not None and self._pending.owner == owner:
                self._clear_pending(outcome="cancelled", reason="user_cancelled")

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
        await self._async_resume(current_cap, grant_release=False)

    async def async_start_guest_charging(self, *, current_cap: float | None = None) -> None:
        """Grant release and resume as one deferred, connection-bound request."""
        await self._async_resume(current_cap, grant_release=True)

    async def async_end_soc_stop_pause(self) -> None:
        """End the pause the charge target's stop wrote; any other pause stays."""
        await self._async_resume(None, grant_release=False, soc_stop_only=True)

    async def _async_resume(
        self, current_cap: float | None, *, grant_release: bool, soc_stop_only: bool = False,
    ) -> None:
        self._require_write_access()
        async with self._current_lock:
            if soc_stop_only and not (
                self.pause_owner == "soc_stop" and self.pause_requested is True
                and self._pending is None and not self.energy_goal_stop_requested
            ):
                return
            maximum = self._resume_current_maximum(current_cap)
            target = self.charging_setpoint
            if target is None:
                target = maximum
            elif target != CHARGING_CURRENT_UNLIMITED:
                target = max(CHARGING_CURRENT_MINIMUM, min(target, maximum))
            await self.queue.async_submit_locked(PendingCommand(
                "resume", target, grant_release=grant_release,
                source="guest" if grant_release else command_source.get(),
            ))

    async def async_set_charging_release(self, released: bool) -> None:
        """Open or close the charging relay through 0x0D05."""

        self._require_write_access()
        async with self._current_lock:
            gate = self._plug_charge.state
            manual = gate.manual_marks(self.write_state.plug_cycle)
            if gate.enabled:
                if released and self.energy_goal_stop_requested:
                    raise self.stop_error()
                # With Plug & Charge, a manual release opens the gate for the
                # current or the next connection, for any car. Withdrawing it
                # closes the gate only with its own write: a gate closed
                # while that still waits would switch inside the interval.
                if released:
                    self._plug_charge.set_manual(True)
            written = self._release_writes
            try:
                await self.queue.async_submit_locked(PendingCommand(
                    "enable_charging" if released else "disable_charging", int(released),
                ))
            except BaseException:
                # A failure after the acknowledged write, e.g. of the refresh,
                # must not make the gate close what the user just opened.
                if self._release_writes == written:
                    gate.restore_manual(manual, self.write_state.plug_cycle)
                raise

    async def async_set_lock_evse(self, locked: bool) -> None:
        """Lock or unlock the wallbox through 0x0D06."""

        self._require_write_access()
        async with self._current_lock:
            await self.queue.async_submit_locked(PendingCommand("lock" if locked else "unlock", int(locked)))

    async def async_set_solar_charging_mode(self, mode: SolarChargingMode) -> None:
        """Set the solar charging mode shown on the wallbox (0x0D03).

        It drives the wallbox display and its solar LEDs; the energy manager
        stays responsible for the solar algorithm, and the wallbox needs DIP 7
        on for the mode to have any effect. It switches no charge, so it
        waits for no state-change interval.
        """
        self._require_write_access()
        async with self._current_lock:
            await self.writes.async_write(SOLAR_CHARGING_MODE, int(mode))

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
            await self.queue.async_submit_locked(PendingCommand(
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
                release = self.charging_release_requested
                if release is None:
                    # Nobody decided the release yet: it stays what the wallbox
                    # has. Recovering opens nothing that was closed.
                    opened = await self._client.async_read_register(CHARGING_RELEASE)
                    if type(opened) is not int or opened not in (0, 1):
                        raise validation_error("recovery_state_unknown")
                    release = bool(opened)
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
            # A planner's current is reasserted as it is: the chosen one is
            # not the line's while a plan runs.
            remembered = None if self.writes.line_planned else self.charging_setpoint
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
                await self.writes.async_write(HEARTBEAT, HEARTBEAT_VALUE)
                # Error 200 requires release before the current register. The
                # current step below reasserts a goal stop as the pause value;
                # rejecting this recovery-only release would leave it faulted.
                await self.writes.async_write(
                    CHARGING_RELEASE, int(release and not self._plug_charge.state.blocked),
                    recovery_release=True,
                )
                await self.writes.async_apply_current(
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
            await self._async_require_restart_idle()
            await self.writes.async_write(
                SYSTEM_RESTART, SYSTEM_RESTART_VALUE, precondition=self._async_require_restart_idle,
            )

    async def _async_require_restart_idle(self, read: LockedRegisterReader | None = None) -> None:
        state = await self.writes.async_current_evse_state("restart_state_unknown", read=read)
        if state != EvseState.IDLE:
            raise validation_error("restart_requires_idle")

    async def async_shutdown(self) -> None:
        """Cancel deferred work; a new owner never inherits a delayed start."""

        await self._plug_charge.async_stop()
        self._clear_pending(outcome="cancelled", reason="shutdown")
        task = self._flush_task
        self._flush_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def async_unplugged(self) -> bool:
        """Whether the wallbox confirms right now that no car is plugged in.

        An older wallbox has no CP state; its idle EVSE state is all it can say.
        """
        return await self.writes.async_vehicle_absent(cp_required=False)

    async def async_close_release(self, source: str) -> None:
        """Switch the release off for a rule of the service itself.

        The caller holds ``energy_goal_guard``. This is the ordinary stop:
        where the wallbox cannot prove that no car is there, it waits for the
        state-change interval (``AmtronRateLimitedError``) like any other.
        """
        if self._pending is not None and self._pending.action == "enable_charging":
            # A release queued for the car that left must not open it later.
            self._clear_pending(outcome="cancelled")
        written = self._release_writes
        await self.writes.async_apply_release(False)
        if self._release_writes != written:
            self.events.record("executed", "disable_charging", source=source)
