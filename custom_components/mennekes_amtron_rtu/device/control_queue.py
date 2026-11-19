"""Run a command now, or keep the one pending command until it may run."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .client_errors import (
    AmtronBusError,
    AmtronRateLimitedError,
)
from .const import (
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
)
from .control_types import (
    OTHER_STOPS,
    STOPS,
    PendingCommand,
    PhaseWaitError,
    TimedCommandCancelledError,
)
from .errors import DeviceError
from .runtime_events import EventType

if TYPE_CHECKING:
    from .control import AmtronControl

_LOGGER = logging.getLogger(__name__)


class CommandQueue:
    """Submits commands to the write path and defers those that must wait."""

    def __init__(self, control: AmtronControl) -> None:
        self._control = control

    async def async_observe_command(self, command: PendingCommand, *, deferred: bool = False) -> None:
        """Report the bus outcome independently of status/persistence callbacks."""
        self._control._active_command = command
        self._control._keep_pending = self._outlasts(command)
        self._control._command_acknowledged = False
        self._control._guest_release_applied = False
        self._control._command_write_started = False
        try:
            self._control._check_command_context()
            if deferred and self._control._validate_deferred is not None:
                self._control._validate_deferred()
            await self._control.writes.async_apply_command(command)
        except TimedCommandCancelledError as err:
            self._control.events.record("cancelled", command.action, err.reason, source=command.source, requested_value=command.value)
        except (PhaseWaitError, AmtronRateLimitedError):
            raise
        except (DeviceError, AmtronBusError, OSError, RuntimeError, ValueError, asyncio.CancelledError) as err:
            key = err.translation_key if isinstance(err, DeviceError) else ""
            reason = {
                "phase_pause_timeout": "phase_timeout",
                "phase_pause_state_unknown": "phase_unconfirmed",
                "phase_state_unknown": "phase_unconfirmed",
                "read_only_mode": "permission_denied",
                "energy_goal_stop_active": "energy_goal",
                "time_limit_reached": "time_limit",
            }.get(key, "write_rejected" if not self._control._uncertain(err) else "connection_error")
            if self._control._command_acknowledged:
                self._control.events.record("executed", command.action, "status_refresh_failed", source=command.source, requested_value=command.value)
            else:
                uncertain = self._control._guest_release_applied or (self._control._command_write_started and self._control._uncertain(err))
                self._control.events.record("uncertain" if uncertain else "failed", command.action, reason, source=command.source, requested_value=command.value)
            raise
        else:
            self._control._record_replaced(command)
            self._control.events.record("executed", command.action, command.completion_reason, source=command.source, requested_value=command.value)
        finally:
            self._control._active_command = None
            self._control._keep_pending = False

    def _outlasts(self, command: PendingCommand) -> bool:
        """Whether the waiting command is a stop that this one does not settle.

        A waiting pause outlasts a current that switches nothing, such as a
        surplus adjustment; a start after it is the newer wish. A waiting lock
        or withdrawn release outlasts whatever happens to the current.
        """
        pending = self._control._pending
        if pending is None or pending == command or pending.action not in STOPS:
            return False
        if pending.action in OTHER_STOPS:
            return command.action in ("set_current", "pause", "resume")
        return command.action == "set_current" and not self._control.writes._current_transition(command.value)

    async def async_submit_locked(self, command: PendingCommand, *, automatic: bool = False) -> None:
        """Accept the latest request, coalescing all deferred commands together."""
        if not automatic:
            self._control._write_state.command_intent_generation += 1
            if self._control.goal_controller is not None:
                await self._control.goal_controller.async_cancel_recovery_locked()
        if self._control._phase_change is not None and command != self._control._pending:
            self._control._record_replaced(command)
            await self.async_cancel_pending()
        had_pending = self._control._pending is not None
        kept = self._outlasts(command)
        try:
            await self.async_observe_command(command)
        except PhaseWaitError as err:
            await self._async_queue(command, err.retry_after, preserve_deadline=err.preserve_deadline)
            return
        except AmtronRateLimitedError as err:
            self._control._diagnostics.writes_rate_limited += 1
            self._control._diagnostics.timeline.record(EventType.WRITE_RATE_LIMITED)
            if command.action in ("set_current", "resume") and command.owner is None:
                self._control.charging_setpoint = command.value
            if kept and command.action == "set_current":
                # The stop keeps its place; the current is remembered above.
                return
            await self._async_queue(command, err.retry_after)
            return
        except (DeviceError, asyncio.CancelledError) as err:
            if not kept and (self._control._phase_change is not None or self._control._uncertain(err)):
                self._control._clear_pending()
                if had_pending:
                    await self._async_publish_pending()
            raise
        if not kept:
            await self.async_cancel_pending()

    async def _async_queue(
        self, command: PendingCommand, delay: float, *, preserve_deadline: bool = False,
    ) -> None:
        due = self._control._monotonic() + delay
        if preserve_deadline and self._control._pending == command:
            self._control._pending_due = due
            return
        if self._control._pending == command and abs(self._control._pending_due - due) < 0.01:
            return
        self._control._record_replaced(command)
        self._control._pending = command
        self._control._pending_due = due
        self._control._pending_until = (
            None if self._control._phase_change is not None and self._control._phase_change.ready_at is None
            else datetime.now(UTC) + timedelta(seconds=delay)
        )
        self._control._pending_changed.set()
        if self._control._flush_task is None or self._control._flush_task.done():
            self._control._flush_task = asyncio.create_task(
                self.async_flush_pending(), name="mennekes_amtron_rtu_control_flush"
            )
        await self._async_publish_pending()

    async def _async_publish_pending(self) -> None:
        if self._control._request_refresh is not None:
            try:
                await self._control._request_refresh()
            except (AmtronBusError, OSError):
                # A subsequent normal poll publishes the cleared/queued
                # status. Do not retry a command because visibility failed.
                _LOGGER.debug("Unable to refresh queued command status")

    async def async_cancel_pending(self) -> None:
        if self._control._clear_pending():
            await self._async_publish_pending()

    async def _async_wait_pending(self, delay: float) -> None:
        sleeping = asyncio.ensure_future(self._control._sleep(delay))
        changed = asyncio.create_task(self._control._pending_changed.wait())
        try:
            await asyncio.wait((sleeping, changed), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeping, changed):
                task.cancel()
            await asyncio.gather(sleeping, changed, return_exceptions=True)

    def _usable(self) -> bool:
        """Whether a command that waited may reach the wallbox right now."""
        if self._control._validate_deferred is None:
            return True
        try:
            self._control._validate_deferred()
        except DeviceError:
            return False
        return True

    async def async_flush_pending(self) -> None:
        try:
            while True:
                async with self._control._current_lock:
                    command = self._control._pending
                    if command is None:
                        return
                    if reason := self._control._command_cancellation_reason(command):
                        self._control._clear_pending(outcome="cancelled", reason=reason)
                        await self._async_publish_pending()
                        return
                    delay = self._control._pending_due - self._control._monotonic()
                    check_disconnect = (
                        self._control._phase_change.ready_at is not None
                        if self._control._phase_change is not None
                        else self._control._mode_change_remaining() > 0
                    )
                    if delay > 0 and check_disconnect and await self._control.writes.async_vehicle_absent():
                        delay = 0
                    if delay <= 0 and command.action in STOPS and not self._usable():
                        # A stop that came due during a lost poll waits for the
                        # wallbox instead of being dropped; a start would not.
                        delay = MIN_CURRENT_WRITE_INTERVAL_SECONDS
                    if delay <= 0:
                        try:
                            await self.async_observe_command(command, deferred=True)
                        except PhaseWaitError as err:
                            await self._async_queue(
                                command, err.retry_after, preserve_deadline=err.preserve_deadline,
                            )
                            delay = err.retry_after
                        except AmtronRateLimitedError as err:
                            await self._async_queue(command, err.retry_after)
                            delay = err.retry_after
                        except (DeviceError, AmtronBusError, OSError) as err:
                            if command.action in STOPS and self._control._uncertain(err):
                                # A stop may be written twice; it must not be
                                # lost with a reply that did not come.
                                delay = MIN_CURRENT_WRITE_INTERVAL_SECONDS
                                await self._async_queue(command, delay)
                            else:
                                # One attempt only. A lost acknowledgement must
                                # never produce an automatic duplicate start.
                                _LOGGER.warning(
                                    "Control: queued %s command cancelled (%s)", command.action,
                                    err.translation_key if isinstance(err, DeviceError)
                                    else type(err).__name__,
                                )
                                self._control._clear_pending()
                                await self._async_publish_pending()
                                return
                        else:
                            await self.async_cancel_pending()
                            return
                    self._control._pending_changed.clear()
                # Recheck unplugging without publishing a changing countdown.
                await self._async_wait_pending(min(delay, MIN_CURRENT_WRITE_INTERVAL_SECONDS))
        except (OSError, RuntimeError, ValueError) as err:
            # A failing refresh/persistence callback must not leave an
            # unobserved task or a queued start with no owner. Never retry it.
            self._control._clear_pending()
            _LOGGER.warning("Control: deferred command cancelled (%s)", type(err).__name__)
        finally:
            self._control._flush_task = None
