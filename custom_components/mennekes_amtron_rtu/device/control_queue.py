"""Run a command now, or keep the one pending command until it may run."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from .client_errors import (
    AmtronBusError,
    AmtronRateLimitedError,
)
from .const import (
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
)
from .control_state import (
    _PendingCommand,
    _PhaseWaitError,
    _TimedCommandCancelledError,
)
from .control_write import WritePath
from .errors import DeviceError
from .runtime_events import EventType

_LOGGER = logging.getLogger(__name__)


class CommandQueue(WritePath):
    """Submits commands to the write path and defers those that must wait."""

    async def _async_observe_command(self, command: _PendingCommand, *, deferred: bool = False) -> None:
        """Report the bus outcome independently of status/persistence callbacks."""
        self._active_command = command
        self._command_acknowledged = False
        self._guest_release_applied = False
        self._command_write_started = False
        try:
            self._check_command_context()
            if deferred and self._validate_deferred is not None:
                self._validate_deferred()
            await self._async_apply_command(command)
        except _TimedCommandCancelledError as err:
            self.events.record("cancelled", command.action, err.reason, source=command.source, requested_value=command.value)
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
                "time_limit_reached": "time_limit",
            }.get(key, "write_rejected" if not self._uncertain(err) else "connection_error")
            if self._command_acknowledged:
                self.events.record("executed", command.action, "status_refresh_failed", source=command.source, requested_value=command.value)
            else:
                uncertain = self._guest_release_applied or (self._command_write_started and self._uncertain(err))
                self.events.record("uncertain" if uncertain else "failed", command.action, reason, source=command.source, requested_value=command.value)
            raise
        else:
            self._record_replaced(command)
            self.events.record("executed", command.action, command.completion_reason, source=command.source, requested_value=command.value)
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
                    if reason := self._command_cancellation_reason(command):
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
                                "Control: queued %s command cancelled (%s)", command.action,
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
            _LOGGER.warning("Control: deferred command cancelled (%s)", type(err).__name__)
        finally:
            self._flush_task = None
