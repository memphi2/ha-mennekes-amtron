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
import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from .capabilities import phase_switch_is_supported
from .client import LockedRegisterReader, WritePrecondition
from .client_errors import (
    AmtronBusError,
    AmtronIdentityError,
    AmtronRateLimitedError,
)
from .const import (
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_UNLIMITED,
    CHARGING_PAUSE_CURRENT,
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
    MIN_PHASE_CHANGE_INTERVAL_SECONDS,
)
from .control_types import (
    PendingCommand,
    PhaseChange,
    PhaseWaitError,
    TimedCommandCancelledError,
)
from .enums import CpState, EvseState
from .errors import DeviceError, device_error, validation_error
from .registers import (
    CHARGING_CURRENT_EMS,
    CHARGING_RELEASE,
    CP_STATE,
    EVSE_STATE,
    HEARTBEAT,
    LOCK_EVSE,
    REQUESTED_PHASES,
    RegisterSpec,
)
from .runtime_events import EventType

if TYPE_CHECKING:
    from .control import AmtronControl

_PHASE_PAUSE_TIMEOUT = 30.0


class WritePath:
    """Every register write of the control, with its interlocks and write intervals."""

    def __init__(self, control: AmtronControl) -> None:
        self._control = control
        # The stop whose reply did not come: its repeat is the same switch,
        # not a new one, and does not wait for the interval it started.
        self._unanswered_stop: tuple[int, int, float | None] | None = None
        # The current on the line is a planner's, not one somebody chose. A
        # flag and no value: the wallbox reads a current back as a float32.
        self.line_planned = False

    async def async_current_evse_state(
        self, error_key: str, *, read: LockedRegisterReader | None = None,
    ) -> EvseState:
        """Confirm the device state immediately before a guarded operation."""

        try:
            state = await (read or self._control._client.async_read_register)(EVSE_STATE)
        except AmtronBusError as err:
            raise validation_error(error_key) from err
        if not isinstance(state, int) or state not in EvseState:
            raise validation_error(error_key)
        return EvseState(state)

    def _current_transition(self, value: float) -> bool:
        paused = self._control.is_paused
        if self._control.pause_requested is True:
            paused = True
        target_paused = 0 < value < CHARGING_CURRENT_MINIMUM
        return paused is None or paused != target_paused

    async def async_vehicle_absent(self, read: LockedRegisterReader | None = None) -> bool:
        """Grant an exception only from fresh, consistent EVSE and CP readings."""
        if self._control._identity.layout_version < CP_STATE.min_layout:
            return False
        read = read or self._control._client.async_read_register
        try:
            state = await read(EVSE_STATE)
            if type(state) is not int or state != EvseState.IDLE:
                return False
            cp = await read(CP_STATE)
        except AmtronBusError:
            return False
        return type(cp) is int and cp in (CpState.A1, CpState.A2)

    async def _async_release_off(self) -> bool:
        """With 0x0D05 off, a current change switches no charge."""
        try:
            value = await self._control._client.async_read_register(CHARGING_RELEASE)
        except AmtronBusError:
            return False
        return type(value) is int and value == 0

    async def async_release_switches_charge(self, read: LockedRegisterReader | None = None) -> bool:
        """With no vehicle or a confirmed pause, 0x0D05 switches no charge.

        The pause holds the EV at 0 A whichever way the relay goes. The later
        resume is the real start and keeps the state-change interval.
        """
        if await self.async_vehicle_absent(read):
            return False
        try:
            current = await (read or self._control._client.async_read_register)(CHARGING_CURRENT_EMS)
        except AmtronBusError:
            return True
        return not (
            not isinstance(current, bool) and isinstance(current, (int, float))
            and CHARGING_CURRENT_UNLIMITED < current < CHARGING_CURRENT_MINIMUM
        )

    def _repeats_stop(self, spec: RegisterSpec, value: int) -> bool:
        """Whether this stop is the one whose reply did not come, with nothing since."""
        return self._unanswered_stop == (
            spec.address, value, self._control._write_state.mode_change_at,
        )

    async def async_check_transition_interval(self, *, current_change: bool = False) -> None:
        remaining = self._control._mode_change_remaining()
        if remaining > 0 and (
            await self.async_vehicle_absent()
            or (current_change and await self._async_release_off())
        ):
            return
        remaining = self._control._mode_change_remaining()
        if remaining > 0:
            raise AmtronRateLimitedError("state_change", remaining)

    async def async_apply_current(
        self, value: float, *, preserve_setpoint: bool = False, recovery: bool = False,
        phase_preparation: bool = False, energy_goal_stop: bool = False,
    ) -> None:
        # A goal stop reaches this method only when a current pause was not
        # freshly confirmed. Treat its write as a possible transition even if
        # the old cached snapshot still says "paused".
        transition = not recovery and (energy_goal_stop or self._current_transition(value))
        paused = 0 < value < CHARGING_CURRENT_MINIMUM
        previous = self._control._write_state.confirmed_current
        if transition and not phase_preparation and not energy_goal_stop:
            await self.async_check_transition_interval(current_change=True)
        if not paused and self._control.energy_goal_stop_requested:
            raise self._control.stop_error()
        try:
            await self.async_write(
                CHARGING_CURRENT_EMS, value,
                min_interval=MIN_CURRENT_WRITE_INTERVAL_SECONDS, refresh=False,
            )
        except (DeviceError, asyncio.CancelledError) as err:
            if self._control._uncertain(err):
                if not recovery:
                    # Recovery is no command: what waits in the queue stays.
                    self._control._clear_pending()
                self._control._write_state.confirmed_current = None
                if not paused:
                    self._remember(value, recovery)
                if transition:
                    self._control._write_state.mode_change_at = self._control._monotonic()
                if paused:
                    self._control.pause_requested = True
                    self._remember_pause_setpoint(previous, preserve_setpoint)
            raise
        self._control._write_state.confirmed_current = value
        self._control.pause_requested = paused
        if not paused:
            self._remember(value, recovery)
        else:
            self._remember_pause_setpoint(previous, preserve_setpoint)
        if transition:
            # The switch counts from its acknowledgement: a cancellation during
            # the reads below must not let the next switch follow at once.
            state = self._control._write_state
            before, state.mode_change_at = state.mode_change_at, self._control._monotonic()
            # Without a vehicle or with the release off no charge was switched, so
            # e.g. Plug & Charge can end a pause before its grant without delay.
            if not phase_preparation and (
                await self.async_vehicle_absent() or await self._async_release_off()
            ):
                state.mode_change_at = before
        if not recovery and not phase_preparation:
            # The command reached the device. A failed visibility/persistence
            # callback must never allow an older queued command to run later.
            self._control._clear_pending()
        if self._control._request_refresh is not None:
            await self._control._request_refresh()

    def _remember(self, value: float, recovery: bool) -> None:
        """Keep a current as the one a later resume returns to.

        Only a current somebody chose: a planner's is its own for the time of
        its plan and must not outlive it. Lowering a planner's line to the cap
        or reasserting it after an error chooses nothing either.
        """
        active = self._control._active_command
        if active is not None and active.owner is not None:
            # Not saved: after a restart a pause takes the current it finds,
            # as it does for one that another controller wrote.
            self.line_planned = True
        elif self.line_planned and (recovery or (active is not None and active.keeps_setpoint)):
            return
        else:
            self._control.charging_setpoint = value
            self.line_planned = False

    def _remember_pause_setpoint(
        self, previous: float | None, preserve_setpoint: bool
    ) -> None:
        """Retain the confirmed current even when the pause acknowledgement is lost."""

        if self.line_planned and self._control.charging_setpoint is not None:
            # The current on the line is a planner's; the chosen one stays.
            return
        if (
            (not preserve_setpoint or self._control.charging_setpoint is None)
            and previous is not None
            and math.isfinite(previous)
            and (previous == CHARGING_CURRENT_UNLIMITED or previous >= CHARGING_CURRENT_MINIMUM)
        ):
            # More than 32 A is a foreign controller's value, no limit to keep.
            self._control.charging_setpoint = min(float(previous), 32.0)

    async def async_read_switch(self, spec: RegisterSpec, *, stopping: bool = False) -> int | None:
        """Recheck live state, but a failed read must not prevent a stop."""
        try:
            value = await self._control._client.async_read_register(spec)
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
        self, spec: RegisterSpec, value: int, *, changed: bool | Callable[[], bool],
        min_interval: float = 0.0,
        precondition: WritePrecondition | None = None,
    ) -> None:
        """Write a switch; ``changed`` may be decided by the write's precondition."""
        def switched() -> bool:
            return changed() if callable(changed) else changed

        if self._control.energy_goal_stop_requested and (
            (spec == CHARGING_RELEASE and value == 1)
            or (spec == LOCK_EVSE and value == 0)
        ):
            raise self._control.stop_error()
        try:
            await self.async_write(
                spec, value, min_interval=min_interval, refresh=False, precondition=precondition,
            )
        except (DeviceError, asyncio.CancelledError) as err:
            if switched() and self._control._uncertain(err):
                self._control._write_state.mode_change_at = self._control._monotonic()
                self._unanswered_stop = (spec.address, int(value), self._control._write_state.mode_change_at)
            raise
        self._unanswered_stop = None
        if spec == CHARGING_RELEASE:
            self._control._release_writes += 1
            if value == 1:
                self._control._plug_charge.release_written()
            elif (active := self._control._active_command) is not None and active.action == "disable_charging":
                # Withdrawn by hand: the gate closes with the acknowledged
                # write, whatever the refresh after it does.
                self._control._plug_charge.set_manual(False)
        if switched():
            self._control._write_state.mode_change_at = self._control._monotonic()
        if self._control._active_command is not None:
            # A write outside a command, e.g. closing the release after an
            # unplug, settles nothing that waits in the queue.
            self._control._clear_pending()
        if self._control._request_refresh is not None:
            await self._control._request_refresh()

    async def async_apply_release(self, released: bool) -> None:
        if released:
            self._control._plug_charge.check_release()
        current = await self.async_read_switch(CHARGING_RELEASE, stopping=not released)
        switches = False

        async def admit(read: LockedRegisterReader) -> None:
            # Decided on the bus right before the write, so a car plugged in
            # meanwhile counts: only a change that can switch a charge waits
            # for, and restarts, the state-change interval.
            nonlocal switches
            switches = await self.async_release_switches_charge(read)
            if switches and (remaining := self._control._mode_change_remaining()) > 0:
                raise AmtronRateLimitedError("state_change", remaining)

        if not released and current != 0:
            # A stop keeps the plain write path: a link that drops during
            # guarded reads must not hold it back.
            switches = await self.async_release_switches_charge()
            if switches and not self._repeats_stop(CHARGING_RELEASE, 0):
                await self.async_check_transition_interval()
        previous = self._control.charging_release_requested
        self._control.charging_release_requested = released
        try:
            await self._async_transition_write(
                CHARGING_RELEASE, int(released), changed=lambda: switches,
                precondition=admit if released and current != 1 else None,
            )
        except (AmtronRateLimitedError, TimedCommandCancelledError):
            # Nothing was sent; the request is not an intent yet.
            self._control.charging_release_requested = previous
            raise
        except (DeviceError, asyncio.CancelledError) as err:
            # A failed block retains its intent unless identity verification
            # proves that no command could have reached the intended device.
            if released or isinstance(err.__cause__, AmtronIdentityError):
                self._control.charging_release_requested = previous
            raise

    async def _async_apply_lock(self, locked: bool) -> None:
        current = await self.async_read_switch(LOCK_EVSE, stopping=locked)
        changed = current != int(locked)
        if changed and not (locked and self._repeats_stop(LOCK_EVSE, 1)):
            await self.async_check_transition_interval()
        await self._async_transition_write(LOCK_EVSE, int(locked), changed=changed)

    async def _async_apply_phases(self, phases: int) -> None:
        current = await self.async_read_switch(REQUESTED_PHASES)
        if current == phases:
            return
        change = self._control._phase_change
        if change is None:
            # Admit the whole pause/phase operation once. Its own pause must
            # not trigger another long wait within this operation.
            await self.async_check_transition_interval()
            self._control._client.check_write_interval(
                REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS
            )
            state = await self.async_current_evse_state("phase_state_unknown")
            if state not in (EvseState.IDLE, EvseState.EV_CONNECTED,
                             EvseState.PRECONDITIONS_VALID, EvseState.READY_TO_CHARGE,
                             EvseState.CHARGING):
                raise validation_error("phase_pause_state_unknown")
            if (
                state == EvseState.IDLE
                and self._control._identity.layout_version >= CP_STATE.min_layout
                and not await self.async_vehicle_absent()
            ):
                raise validation_error("phase_pause_state_unknown")
            if state != EvseState.IDLE:
                # Preparation physically pauses charging. Discard the older
                # command before that write, including if persistence fails.
                self._control._clear_pending()
                await self.async_apply_current(CHARGING_PAUSE_CURRENT, phase_preparation=True)
                change = PhaseChange(self._control._monotonic() + _PHASE_PAUSE_TIMEOUT)
                self._control._phase_change = change
        if change is not None:
            await self._async_confirm_phase_pause(change)
        # Every resumed step rechecks live state and hardware identity through
        # the client; no register lock is held during the waiting periods.
        self._control._client.check_write_interval(
            REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS
        )
        await self._async_transition_write(
            REQUESTED_PHASES, phases, changed=True,
            min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS,
            precondition=lambda read: self._async_check_phase_write(change, read),
        )

    async def _async_check_phase_write(
        self, change: PhaseChange | None, read: LockedRegisterReader,
    ) -> None:
        """Revalidate the relevant status while holding the final bus transaction."""
        if change is not None:
            await self._async_confirm_phase_pause(change, read=read)
            return
        state = await self.async_current_evse_state("phase_pause_state_unknown", read=read)
        if state != EvseState.IDLE:
            raise validation_error("phase_pause_state_unknown")
        if self._control._identity.layout_version >= CP_STATE.min_layout:
            try:
                cp = await read(CP_STATE)
            except AmtronBusError as err:
                raise validation_error("phase_pause_state_unknown") from err
            if type(cp) is not int or cp not in (CpState.A1, CpState.A2):
                raise validation_error("phase_pause_state_unknown")

    async def _async_confirm_phase_pause(
        self, change: PhaseChange, *, read: LockedRegisterReader | None = None,
    ) -> None:
        read = read or self._control._client.async_read_register
        state = await self.async_current_evse_state("phase_pause_state_unknown", read=read)
        if state in (EvseState.NOT_INITIALIZED, EvseState.ERROR, EvseState.SERVICE_MODE):
            raise validation_error("phase_pause_state_unknown")
        if change.unplug_confirmed and state != EvseState.IDLE:
            # An observed unplug ends this vehicle's phase operation. A later
            # connection may be another car and cannot inherit its grace bypass.
            raise validation_error("phase_pause_state_unknown")
        paused = state in (EvseState.IDLE, EvseState.EV_CONNECTED)
        if self._control._identity.layout_version >= CP_STATE.min_layout:
            try:
                cp = await read(CP_STATE)
            except AmtronBusError as err:
                raise validation_error("phase_pause_state_unknown") from err
            if type(cp) is not int or cp not in CpState or cp in (CpState.INIT, CpState.E, CpState.F):
                raise validation_error("phase_pause_state_unknown")
            paused = paused and (
                cp in (CpState.A1, CpState.A2) if state == EvseState.IDLE else cp == CpState.B1
            )
        now = self._control._monotonic()
        if paused and state == EvseState.IDLE:
            # Unplugging also ends an already pending vehicle grace period.
            change.unplug_confirmed = True
            change.ready_at = now
            return
        if not paused:
            if change.ready_at is not None:
                raise validation_error("phase_pause_state_unknown")
            if now >= change.confirmation_deadline:
                raise validation_error("phase_pause_timeout")
            raise PhaseWaitError(min(1.0, change.confirmation_deadline - now), preserve_deadline=True)
        if change.ready_at is None:
            change.ready_at = now + self._control._options.phase_switch_grace_seconds
        remaining = change.ready_at - now
        if remaining > 0:
            raise PhaseWaitError(remaining)

    async def async_apply_command(self, command: PendingCommand) -> None:
        self._control._require_write_access()
        if (
            command.recovery_generation is not None
            and command.recovery_generation != self._control._write_state.energy_goal_generation
        ):
            raise validation_error("energy_goal_stop_active")
        if self._control.energy_goal_stop_requested and command.action in (
            "set_current", "resume", "enable_charging", "unlock",
        ):
            raise self._control.stop_error()
        if command.action == "pause":
            await self.async_apply_current(
                CHARGING_PAUSE_CURRENT, energy_goal_stop=command.energy_goal_stop,
            )
        elif command.action in ("set_current", "resume"):
            if command.grant_release:
                await self._async_grant_release_for_start()
            # A configuration change can lower the available current while
            # an accepted request is waiting. Reapply the active limit.
            await self.async_apply_current(min(command.value, self._control.max_charging_current))
        elif command.action in ("single_phase", "all_phases"):
            if not phase_switch_is_supported(self._control._identity.phase_options_hw):
                raise validation_error("phase_switch_unsupported")
            await self._async_apply_phases(int(command.value))
        elif command.action in ("enable_charging", "disable_charging"):
            await self.async_apply_release(bool(command.value))
        else:
            await self._async_apply_lock(bool(command.value))

    async def _async_grant_release_for_start(self) -> None:
        """Keep release and resume in one queued command, under the same lock."""
        gate = self._control._plug_charge.state
        manual, written = gate.manual_marks(self._control.write_state.plug_cycle), self._control._release_writes
        if gate.enabled:
            self._control._plug_charge.set_manual(True)
        try:
            await self.async_apply_release(True)
        except BaseException as err:
            refused = isinstance(err, (AmtronRateLimitedError, TimedCommandCancelledError))
            if self._control._release_writes == written and (refused or not self._control._command_write_started or not self._control._uncertain(err)):
                gate.restore_manual(manual, self._control.write_state.plug_cycle)
            raise

    async def async_write(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
        refresh: bool = True,
        recovery_release: bool = False,
        precondition: WritePrecondition | None = None,
    ) -> None:
        if spec == CHARGING_CURRENT_EMS:
            self._control._goal_pause_confirmed = None
        self._control._check_command_context()
        if spec == CHARGING_RELEASE and value == 1:
            self._control._plug_charge.check_release(recovery=recovery_release)
        if self._control._active_command is not None:
            self._control._command_write_started = True
        try:
            bound = self._control._active_command is not None and (self._control._active_command.owner is not None or self._control._active_command.grant_release)

            async def check_preconditions(read: LockedRegisterReader) -> None:
                if bound:
                    await self._async_check_command_vehicle(read)
                if precondition is not None:
                    await precondition(read)

            if self._write_enables_charging(spec, value) or bound or precondition is not None:
                await self._control._client.async_write_register(
                    spec, value, min_interval=min_interval,
                    before_write=lambda: self._check_write_allowed(
                        spec, value, recovery_release=recovery_release,
                    ),
                    precondition=check_preconditions if bound or precondition is not None else None,
                )
            else:
                await self._control._client.async_write_register(
                    spec, value, min_interval=min_interval
                )
        except AmtronRateLimitedError:
            self._control._diagnostics.timeline.record(EventType.WRITE_RATE_LIMITED, register=spec.address)
            raise
        except AmtronBusError as err:
            if isinstance(err, AmtronIdentityError):
                raise device_error("device_identity_changed") from err
            self._control._diagnostics.timeline.record(EventType.WRITE_REJECTED, register=spec.address)
            self._control._diagnostics.writes_rejected += 1
            if spec != HEARTBEAT:
                self._control._diagnostics.write_errors.add(spec.key)
            self._control._diagnostics.last_error = str(err)
            if self._control._notify_write_error is not None:
                self._control._notify_write_error()
            raise device_error(
                "write_failed", {"register": spec.key, "error": str(err)}
            ) from err
        self._control._diagnostics.writes_sent += 1
        if self._control._active_command is not None:
            action = self._control._active_command.action
            expected = (
                CHARGING_CURRENT_EMS if action in ("pause", "resume", "set_current")
                else REQUESTED_PHASES if action in ("single_phase", "all_phases")
                else CHARGING_RELEASE if action in ("enable_charging", "disable_charging")
                else LOCK_EVSE
            )
            if self._control._active_command.grant_release and spec == CHARGING_RELEASE:
                self._control._guest_release_applied = True
            if spec == expected:
                self._control._command_acknowledged = True
        if spec.key in self._control._diagnostics.write_errors:
            self._control._diagnostics.write_errors.remove(spec.key)
            if self._control._notify_write_error is not None:
                self._control._notify_write_error()
        if refresh and self._control._request_refresh is not None:
            await self._control._request_refresh()

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
        # A grant is permitted only when no active charging limit blocks it.
        grant = (
            spec == CHARGING_RELEASE and value == 1 and self._control._plug_charge.authorizing
            and not self._control.release_stop_requested
        )
        self._control._check_command_context()
        if spec == CHARGING_RELEASE and value == 1:
            self._control._plug_charge.check_release(recovery=recovery_release)
        if (
            self._write_enables_charging(spec, value) and self._control.energy_goal_stop_requested
            and not (recovery_release and spec == CHARGING_RELEASE) and not grant
        ):
            raise self._control.stop_error()

    async def _async_check_command_vehicle(self, read: LockedRegisterReader) -> None:
        """Cancel a bound command if live EVSE and CP both prove unplugging."""
        if await self.async_vehicle_absent(read):
            raise TimedCommandCancelledError("unknown")
