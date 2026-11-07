"""Bind vehicle percentage ceilings to the shared command owner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import copy

from .client_errors import AmtronBusError, AmtronRateLimitedError
from .control import AmtronControl
from .errors import DeviceError, validation_error
from .persistence import finish_goal_save
from .soc_goal import SocGoal, percentage

_LOGGER = logging.getLogger(__name__)


class SocGoalController:
    """Persist bounded source/goal state, with no timer for missing vehicle data."""

    def __init__(
        self, control: AmtronControl, persist: Callable[[], Awaitable[None]],
        notify: Callable[[], None] | None = None, *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._control = control
        self._persist = persist
        self._notify = notify
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._pending: SocGoal | None = None
        self._pending_generation = 0
        self._pending_soc: float | None = None
        self._pending_maximum: float | None = None
        self._dirty = False
        self._stopping = False
        self._failure_logged = False
        control.soc_goal_controller = self

    @property
    def model(self) -> SocGoal:
        return self._control.soc_goal

    @property
    def stop_requested(self) -> bool:
        return self.model.stop_requested or (
            self._pending is not None
            and self._pending_generation == self._control.write_state.soc_goal_generation
            and self._pending.stop_requested
        )

    async def async_configure_source(self, source: str | None) -> None:
        async with self._control.energy_goal_guard:
            candidate = copy(self.model)
            try:
                changed = candidate.configure_source(source)
            except ValueError as err:
                raise validation_error("soc_goal_invalid") from err
            if changed:
                await self._replace(candidate)

    async def async_set_target(
        self, value: object, *, source: str, soc: object = None, maximum: object = None,
    ) -> None:
        if self._control.read_only:
            raise validation_error("read_only_mode")
        async with self._control.energy_goal_guard:
            self._validate_source(source)
            candidate = copy(self.model)
            try:
                candidate.set_target(value, soc, maximum)
            except ValueError as err:
                raise validation_error("soc_goal_invalid") from err
            await self._replace(candidate)
        self.retry_stop()

    def _validate_source(self, source: str) -> None:
        if not isinstance(source, str) or not source or source != self.model.source:
            raise validation_error("soc_goal_source_changed")

    async def _replace(self, candidate: SocGoal) -> None:
        if self._stopping:
            raise validation_error("soc_goal_unavailable")
        state = self._control.write_state
        original, pending = state.soc_goal, self._pending
        original_dirty = self._dirty
        original_soc, original_maximum = self._pending_soc, self._pending_maximum
        state.soc_goal = candidate
        self._control.cancel_goal_resume()
        state.soc_goal_generation += 1
        self._pending = None
        self._pending_soc = self._pending_maximum = None
        try:
            await finish_goal_save(self._persist())
            self._dirty = False
        except asyncio.CancelledError:
            try:
                if self._control.goal_controller is not None:
                    await self._control.goal_controller.async_cancel_recovery_locked()
            finally:
                # Recovery persistence can itself be cancelled. A committed
                # reached target must still arrange its stop before we exit.
                self.retry_stop()
                self._changed()
            raise
        except Exception:
            restored = copy(pending or original)
            if candidate.source == original.source:
                restored.observe(self._pending_soc, self._pending_maximum)
            self._pending_soc, self._pending_maximum = original_soc, original_maximum
            state.soc_goal = restored
            state.soc_goal_generation += 1
            self._pending = None
            self._dirty = original_dirty or restored.snapshot() != original.snapshot()
            self._changed()
            self.retry_stop()
            raise
        if self._control.goal_controller is not None:
            await self._control.goal_controller.async_cancel_recovery_locked()
        self._control.cancel_goal_resume()
        self._changed()

    async def async_observe(self, soc: object, maximum: object = None, *, source: str) -> None:
        if self._stopping:
            return
        self._validate_source(source)
        state = self._control.write_state
        generation = state.soc_goal_generation
        if self._pending is None or self._pending_generation != generation:
            self._pending = copy(self.model)
            self._pending_generation = generation
            self._pending_soc = self._pending_maximum = None
        try:
            current = percentage(soc)
        except ValueError:
            pass
        else:
            self._pending_soc = max(current, self._pending_soc or 0)
        try:
            cap = percentage(maximum, maximum=True)
        except ValueError:
            pass
        else:
            self._pending_maximum = min(cap, self._pending_maximum or 100)
        self._pending.observe(soc, maximum)
        async with self._control.energy_goal_guard:
            if generation != state.soc_goal_generation:
                return
            pending = self._pending
            self._pending = None
            if pending is None:
                return
            changed = pending.snapshot() != self.model.snapshot()
            state.soc_goal = pending
            self._dirty |= changed
            try:
                if self._dirty:
                    await self._persist()
                    self._dirty = False
            finally:
                if changed:
                    self._changed()
                self.retry_stop()

    def retry_stop(self) -> None:
        """Retry only an idempotent goal pause on a later owner poll."""
        if (
            not self._stopping and self._task is None
            and (self._dirty or (not self._control.read_only and self.stop_requested))
        ):
            self._task = asyncio.create_task(self._pause(), name="amtron_soc_goal")

    async def _pause(self) -> None:
        try:
            while not self._stopping and (self._dirty or self.stop_requested):
                async with self._control.energy_goal_guard:
                    state = self._control.write_state
                    if self._pending is not None and self._pending_generation == state.soc_goal_generation:
                        state.soc_goal = self._pending
                        self._pending = None
                    try:
                        await self._persist()
                        self._dirty = False
                    except (OSError, RuntimeError, ValueError) as err:
                        if not self._failure_logged:
                            _LOGGER.error("Cannot save percentage goal stop (%s)", type(err).__name__)
                            self._failure_logged = True
                if self._control.read_only or not self.stop_requested:
                    return
                try:
                    await self._control.async_pause_for_energy_goal()
                except AmtronRateLimitedError as err:
                    await self._sleep(err.retry_after)
                else:
                    if not self._dirty:
                        self._failure_logged = False
                    return
        except (DeviceError, AmtronBusError, OSError, RuntimeError, ValueError) as err:
            if not self._failure_logged:
                _LOGGER.error("Cannot enforce percentage goal (%s)", type(err).__name__)
                self._failure_logged = True
        finally:
            self._task = None

    def _changed(self) -> None:
        if self._notify is not None:
            try:
                self._notify()
            except Exception:  # noqa: BLE001 - UI failures must not lose a ceiling
                _LOGGER.error("Unable to publish a percentage goal update")

    async def async_stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        async with self._control.energy_goal_guard:
            pending = self._pending
            self._pending = None
            if (
                pending is not None
                and self._pending_generation == self._control.write_state.soc_goal_generation
            ):
                self._dirty |= pending.snapshot() != self.model.snapshot()
                self._control.write_state.soc_goal = pending
            if self._dirty:
                try:
                    await self._persist()
                    self._dirty = False
                except (OSError, RuntimeError, ValueError) as err:
                    _LOGGER.error("Cannot save percentage goal on shutdown (%s)", type(err).__name__)
