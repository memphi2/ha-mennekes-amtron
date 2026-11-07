"""Run a durable energy ceiling alongside the single Modbus command owner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import copy

from .client import MennekesModbusClient
from .client_errors import AmtronBusError, AmtronRateLimitedError
from .control import AmtronControl
from .data import WallboxData
from .energy_goal import EnergyGoal
from .errors import DeviceError, validation_error
from .persistence import finish_goal_save
from .registers import ENERGY_TOTAL

_LOGGER = logging.getLogger(__name__)


class EnergyGoalController:
    """Observe real meter reads and serialize stop decisions with commands.

    A speculative copy folds observations while a command owns the lock. A
    reset, unavailable meter or reached goal remains latched in that copy, so
    a later reading cannot erase the evidence before the worker acquires it.
    Only one copy and one task are retained, independently of polling rate.
    """

    def __init__(
        self,
        control: AmtronControl,
        client: MennekesModbusClient,
        persist: Callable[[], Awaitable[None]],
        notify: Callable[[], None] | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._control = control
        self._client = client
        self._persist = persist
        self._notify = notify
        self._sleep = sleep
        self._pending: EnergyGoal | None = None
        self._pending_generation = 0
        self._pending_intent_generation = 0
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._failure_logged = False
        control.goal_controller = self
        self.model.resume_on_recovery = False

    @property
    def model(self) -> EnergyGoal:
        """Return the current shared goal, including restored or replaced state."""
        return self._control.energy_goal

    @property
    def stop_requested(self) -> bool:
        """Fence waiting starts as soon as terminal evidence has been observed."""
        return self.model.stop_requested or (
            self._pending is not None
            and self._pending_generation == self._client.write_state.energy_goal_generation
            and self._pending.stop_requested
        )

    async def async_set_target(self, value: object) -> None:
        """Persist a new ceiling before it can affect subsequent commands."""
        if self._stopping:
            raise validation_error("energy_goal_unavailable")
        if self._control.read_only:
            raise validation_error("read_only_mode")
        if not self._control.supports_energy_goal:
            raise validation_error("energy_goal_not_supported")
        candidate = EnergyGoal()
        try:
            # Reject invalid input before requesting anything from the wallbox.
            candidate.set_target(value, 0)
        except ValueError as err:
            raise validation_error("energy_goal_invalid") from err
        async with self._control.energy_goal_guard:
            if self._stopping:
                raise validation_error("energy_goal_unavailable")
            if candidate.target_kwh:
                try:
                    total = await self._client.async_read_register(ENERGY_TOTAL, verify_identity=True)
                    candidate.set_target(value, total)
                except (AmtronBusError, ValueError) as err:
                    # A rejected replacement does not erase an existing goal,
                    # but the failed live read is still evidence that its
                    # energy counter can no longer be monitored safely.
                    self.observe(None)
                    raise validation_error("energy_goal_unavailable") from err
            state = self._client.write_state
            original = state.energy_goal
            original_pending = (
                self._pending if self._pending_generation == state.energy_goal_generation else None
            )
            state.energy_goal = candidate
            state.energy_goal_generation += 1
            self._pending = None
            try:
                await finish_goal_save(self._persist())
            except asyncio.CancelledError:
                # Owners shield an atomic save and may report cancellation
                # after it committed. Keep the explicit target in memory too.
                self._control.cancel_goal_resume()
                self._notify_changed()
                raise
            except Exception:
                # No wallbox write occurs while setting a ceiling. Invalidate
                # polls that saw the rejected candidate before restoring intent.
                state.energy_goal = original
                state.energy_goal_generation += 1
                # A failed reconfiguration must not discard evidence already
                # waiting to stop the original goal. Preserve it under the new
                # generation, then include the live read made for this request.
                restored = copy(original_pending or original) if original.target_kwh else None
                if restored is not None:
                    if candidate.baseline_kwh is not None:
                        previous_status = restored.status
                        restored.observe(candidate.baseline_kwh)
                        if previous_status == "meter_unavailable" and restored.status == "active":
                            restored.observe(None)
                    if self._pending is not None and self._pending.stop_requested:
                        restored.observe(None)
                self._pending = restored
                self._pending_generation = state.energy_goal_generation
                self._ensure_task()
                raise
        self._control.cancel_goal_resume()
        self._notify_changed()

    async def async_cancel_recovery_locked(self) -> None:
        """A deliberate command revokes permission to resume our outage pause."""
        changed = self.model.resume_on_recovery
        self.model.resume_on_recovery = False
        if self._pending is not None:
            self._pending.resume_on_recovery = False
        if changed:
            try:
                await self._persist()
            except (OSError, RuntimeError, ValueError) as err:
                # A failed disk must not prevent a deliberate stop command.
                _LOGGER.error("Cannot save cancelled goal recovery (%s)", type(err).__name__)
        self._control.cancel_goal_resume()

    def observe(self, snapshot: WallboxData | None) -> None:
        """Fold new evidence and arrange one worker; cached meter data is ignored."""
        if self._stopping or not self.model.target_kwh:
            return
        generation = self._client.write_state.energy_goal_generation
        if snapshot is not None and snapshot.energy_goal_generation != generation:
            return
        if (
            snapshot is not None and not snapshot.energy_read
            and not self.model.stop_requested and not self.model.resume_on_recovery
        ):
            return
        if self._pending is None or self._pending_generation != generation:
            self._pending = copy(self.model)
            self._pending_generation = generation
            self._pending_intent_generation = self._client.write_state.command_intent_generation
        if snapshot is None or snapshot.energy_read:
            previous = copy(self._pending)
            self._pending.observe(None if snapshot is None else snapshot.get(ENERGY_TOTAL.key))
            if (
                previous.status == "meter_unavailable" and self.model.status == "active"
                and self._pending.status == "active"
            ):
                # Do not erase an outage before the worker has enforced its
                # pause. A later fresh observation can then authorize recovery.
                self._pending = previous
        self._ensure_task()

    def _ensure_task(self) -> None:
        if self._pending is not None and self._task is None:
            self._task = asyncio.create_task(self._run(), name="amtron_energy_goal")

    async def _run(self) -> None:
        try:
            while self._pending is not None and not self._stopping:
                persistence_error: Exception | None = None
                async with self._control.energy_goal_guard:
                    pending, generation = self._pending, self._pending_generation
                    self._pending = None
                    if pending is None or generation != self._client.write_state.energy_goal_generation:
                        continue
                    if (
                        pending.status == "meter_unavailable" and self.model.status == "active"
                        and self._control.pause_requested is not True
                        and self._control.is_paused is False
                        and self._pending_intent_generation == self._client.write_state.command_intent_generation
                        and self._control.pending_action is None
                        and not self._control.soc_goal.stop_requested
                    ):
                        pending.resume_on_recovery = True
                    changed = pending.snapshot() != self.model.snapshot()
                    self._client.write_state.energy_goal = pending
                    if pending.stop_requested:
                        self._control.pause_requested = True
                        # Reattempt a failed persistence before each idempotent
                        # stop retry; stores suppress unchanged disk writes.
                        try:
                            await self._persist()
                        except (OSError, RuntimeError, ValueError) as err:
                            # Disk trouble must not leave an already reached
                            # goal charging. The old durable active baseline
                            # still detects the limit again after a restart.
                            persistence_error = err
                    elif changed or pending.resume_on_recovery:
                        await self._persist()
                    if (
                        pending.status == "active" and pending.resume_on_recovery
                        and await self._control.async_energy_goal_recovery_ready()
                    ):
                        # Consume the transient grant before a possible write.
                        # Resume follows the ordinary vehicle guard and is
                        # invalidated by later explicit goals or commands.
                        pending.resume_on_recovery = False
                        await self._persist()
                        if not self._control.energy_goal_stop_requested:
                            await self._control.async_resume_for_energy_goal_locked(generation)
                    if changed:
                        self._notify_changed()
                if pending.stop_requested:
                    await self._async_pause()
                if persistence_error is not None:
                    raise persistence_error
                self._failure_logged = False
        except (DeviceError, AmtronBusError, OSError, RuntimeError, ValueError) as err:
            if not self._failure_logged:
                _LOGGER.error("Cannot enforce energy goal (%s)", type(err).__name__)
                self._failure_logged = True
            self._notify_changed()
        finally:
            self._task = None

    async def _async_pause(self) -> None:
        while not self._stopping:
            try:
                await self._control.async_pause_for_energy_goal()
            except AmtronRateLimitedError as err:
                # Only the five-second current floor applies here. Every other
                # failed attempt waits for another poll instead of busy-looping.
                await self._sleep(err.retry_after)
            else:
                return

    def _notify_changed(self) -> None:
        if self._notify is not None:
            try:
                self._notify()
            except Exception:  # noqa: BLE001 - UI observers must not affect the stop
                _LOGGER.error("Unable to publish an energy goal update")

    async def async_stop(self) -> None:
        """Stop this owner without discarding the durable ceiling or stop latch."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        async with self._control.energy_goal_guard:
            pending = self._pending
            self._pending = None
            if (
                pending is not None and pending.stop_requested
                and self._pending_generation == self._client.write_state.energy_goal_generation
            ):
                self._client.write_state.energy_goal = pending
                self._control.pause_requested = True
                try:
                    await self._persist()
                except (OSError, RuntimeError, ValueError) as err:
                    _LOGGER.error("Cannot save energy goal stop on shutdown (%s)", type(err).__name__)
