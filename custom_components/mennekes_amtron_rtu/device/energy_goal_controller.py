"""Run a durable energy ceiling alongside the single Modbus command owner."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import copy
from datetime import UTC, datetime

from .client import MennekesModbusClient
from .client_errors import AmtronBusError, AmtronRateLimitedError
from .control import AmtronControl
from .data import WallboxData
from .energy_goal import (
    MAX_PRICE,
    PRICE_INTERVAL_SECONDS,
    PRICE_TOLERANCE,
    CostPart,
    EnergyGoal,
    checked_currency,
    energy_for,
    finite_number,
)
from .errors import DeviceError, validation_error
from .notice import FailureNotice
from .persistence import (
    StorageCommitUncertainError,
    StorageStateUnknownError,
    finish_goal_save,
)
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
        monotonic: Callable[[], float] = time.monotonic,
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
        self._notice = FailureNotice(_LOGGER, "Energy target")
        self._storage_unknown = False
        self._dirty = False
        self._monotonic = monotonic
        self._price_candidate: EnergyGoal | None = None
        self._priced_at = -math.inf
        # The last price Home Assistant reported, per kWh.
        self._price: float | None = None
        self.price_at: datetime | None = None
        # The currency of that price, as Home Assistant names it.
        self._price_currency: str | None = None
        control.goal_controller = self
        self.model.resume_on_recovery = False

    @property
    def model(self) -> EnergyGoal:
        """Return the current shared goal, including restored or replaced state."""
        return self._control.energy_goal

    @property
    def goal_to_save(self) -> EnergyGoal:
        """The proposed price is visible to storage while polls still see the committed goal."""
        return self._price_candidate or self.model

    @property
    def stop_requested(self) -> bool:
        """Fence waiting starts as soon as terminal evidence has been observed."""
        return self._storage_unknown or self.model.stop_requested or (
            self._pending is not None
            and self._pending_generation == self._client.write_state.energy_goal_generation
            and self._pending.stop_requested
        )

    async def _save_state(self) -> None:
        """Fence an unknown disk state until any later save succeeds."""
        try:
            await self._persist()
        except StorageStateUnknownError:
            self._storage_unknown = True
            # A pause required by an unknown store cannot grant automatic
            # recovery later, even if the wallbox was already paused.
            self.model.resume_on_recovery = False
            if self._pending is not None:
                self._pending.resume_on_recovery = False
            try:
                self._control.cancel_goal_resume()
            except Exception as err:  # noqa: BLE001 - retain the storage error
                _LOGGER.error("Energy target: cannot publish cancelled goal recovery (%s)", type(err).__name__)
            raise
        self._storage_unknown = False
        self._dirty = False

    @property
    def currency(self) -> str | None:
        cost = self.model.cost
        return self._price_currency if cost is None else cost.currency

    def _currency_for(self, currency: object) -> str:
        unit = checked_currency(currency)
        cost = self.model.cost
        unit = unit or self.currency
        if unit is None or (cost is not None and unit != cost.currency):
            raise validation_error("cost_goal_price_unknown")
        return unit

    async def async_set_cost_target(self, amount: object, price: object = None, *, currency: object = None) -> None:
        """Arm an amount of money at ``price``; zero ends it with its target.

        Without a price the last one Home Assistant reported applies, so the
        app can arm an amount, too.
        """
        value = finite_number(amount)
        if value is None:
            raise validation_error("cost_goal_invalid")
        if value == 0:
            checked_currency(currency)
            if self.model.cost is not None:
                await self.async_set_target(0)
            return
        denomination = self._currency_for(currency)
        if price is None and denomination != self.currency:
            raise validation_error("cost_goal_price_unknown")
        unit = self.price if price is None else finite_number(price)
        if unit is None or abs(unit) > MAX_PRICE:
            raise validation_error("cost_goal_price_unknown")
        try:
            cost = CostPart(value, 0.0, unit, currency=denomination)
        except ValueError as err:
            raise validation_error("cost_goal_invalid") from err
        try:
            await self.async_set_target(energy_for(cost.amount, cost.price), cost=cost)
        finally:
            # A directory sync error can follow a committed replacement.
            if self.model.cost is cost:
                if price is not None:
                    self._price, self.price_at = unit, datetime.now(UTC)
                self._price_currency = denomination
                self._priced_at = self._monotonic()
                self._notify_changed()

    @property
    def price(self) -> float | None:
        """The last price learned: reported, or the one of the amount.

        It is not saved. After a restart a restored amount still names its
        price; Home Assistant tells a different one again.
        """
        if self._price is not None:
            return self._price
        cost = self.model.cost
        return None if cost is None else cost.price

    async def async_set_price(self, price: object, *, currency: object = None) -> None:
        """Remember a new price; a running amount books the energy so far.

        The running goal keeps its baseline, status, generation and latches;
        only its target and amount change, in the model and in the copy that
        polls fold into meanwhile. At most once a minute, and only for a
        change of 0.1 ct/kWh. The energy so far is the last polled meter.
        """
        unit = finite_number(price)
        if unit is None or abs(unit) > MAX_PRICE:
            raise validation_error("cost_goal_price_unknown")
        # Remembered for an amount armed later, also from the app; it is not
        # saved: after a restart Home Assistant tells it again.
        async with self._control.energy_goal_guard:
            if self._stopping:
                raise validation_error("energy_goal_unavailable")
            denomination = self._currency_for(currency)
            cost = self.model.cost
            if (
                cost is not None and abs(unit - cost.price) >= PRICE_TOLERANCE
                and self._monotonic() - self._priced_at >= PRICE_INTERVAL_SECONDS
            ):
                pending = self._pending if self._pending_generation == self._client.write_state.energy_goal_generation else None
                candidate = copy(pending or self.model)
                if candidate.reprice(unit):
                    await self._save_price(candidate, unit, denomination)
                    return
            self._remember_price(unit, denomination)

    def _remember_price(self, unit: float, currency: str | None) -> None:
        self._price, self._price_currency = unit, currency
        self.price_at = datetime.now(UTC)
        self._notify_changed()

    async def _save_price(self, candidate: EnergyGoal, unit: float, currency: str | None) -> None:
        self._price_candidate = candidate
        try:
            await finish_goal_save(self._save_state())
        except (asyncio.CancelledError, StorageCommitUncertainError, StorageStateUnknownError):
            # Replacement completed, or its outcome is unknown and starts are fenced.
            self._apply_price(candidate, unit, currency)
            if self._storage_unknown:
                self.observe(None)
            raise
        else:
            self._apply_price(candidate, unit, currency)
        finally:
            self._price_candidate = None

    def _apply_price(self, candidate: EnergyGoal, unit: float, currency: str | None) -> None:
        self.model.take_cost(candidate)
        if self._pending is not None and self._pending_generation == self._client.write_state.energy_goal_generation:
            self._pending.take_cost(candidate)
        self._priced_at = self._monotonic()
        self._remember_price(unit, currency)

    async def async_set_target(self, value: object, *, cost: CostPart | None = None) -> None:
        """Persist a new ceiling before it can affect subsequent commands.

        ``cost`` is the amount of money the ceiling stands for.
        """
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
                    candidate.cost = cost
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
                await finish_goal_save(self._save_state())
            except asyncio.CancelledError:
                # Owners shield an atomic save and may report cancellation
                # after it committed. Keep the explicit target in memory too.
                self._storage_unknown = False
                self._control.cancel_goal_resume()
                self._notify_changed()
                raise
            except StorageStateUnknownError:
                # Until a later save succeeds neither the old nor the new
                # on-disk limit can be assumed. Fence starts and schedule a
                # pause, including when the requested candidate is disabled.
                self._storage_unknown = True
                self._dirty = True
                self._pending = copy(candidate)
                self._pending_generation = state.energy_goal_generation
                self._ensure_task()
                try:
                    self._control.cancel_goal_resume()
                except Exception as err:  # noqa: BLE001 - retain the storage error
                    _LOGGER.error("Energy target: cannot publish cancelled goal recovery (%s)", type(err).__name__)
                self._notify_changed()
                raise
            except StorageCommitUncertainError:
                # The new snapshot is already visible on disk; preserve the
                # same intent in memory while still reporting the sync error.
                try:
                    self._control.cancel_goal_resume()
                except Exception as err:  # noqa: BLE001 - retain the storage error
                    _LOGGER.error("Energy target: cannot publish cancelled goal recovery (%s)", type(err).__name__)
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

    def drop_recovery(self) -> bool:
        """Another limit stopped the charge: the outage pause stays paused.

        Nothing is saved or cancelled here; the stop that calls this runs at
        every tick. The grant does not survive a restart anyway. Returns
        whether there was one to drop.
        """
        if self.model.resume_on_recovery or (self._pending is not None and self._pending.resume_on_recovery):
            self.model.resume_on_recovery = False
            if self._pending is not None:
                self._pending.resume_on_recovery = False
            self._dirty = True
            return True
        return False

    async def async_cancel_recovery_locked(self) -> None:
        """A deliberate command revokes permission to resume our outage pause."""
        changed = self.model.resume_on_recovery
        self.model.resume_on_recovery = False
        if self._pending is not None:
            self._pending.resume_on_recovery = False
        if changed:
            try:
                await self._save_state()
            except (OSError, RuntimeError, ValueError) as err:
                # A failed disk must not prevent a deliberate stop command.
                if self._storage_unknown:
                    self._pending = copy(self.model)
                    self._pending_generation = self._client.write_state.energy_goal_generation
                    self._ensure_task()
                _LOGGER.error("Energy target: cannot save cancelled goal recovery (%s)", type(err).__name__)
        # The deliberate command takes the place of a queued automatic one.
        self._control.cancel_goal_resume(replaced=True)

    def observe(self, snapshot: WallboxData | None) -> None:
        """Fold new evidence and arrange one worker; cached meter data is ignored."""
        if self._stopping or (not self.model.target_kwh and not self._storage_unknown):
            return
        generation = self._client.write_state.energy_goal_generation
        if snapshot is not None and snapshot.energy_goal_generation != generation:
            return
        if (
            snapshot is not None and not snapshot.energy_read
            and not self.stop_requested and not self.model.resume_on_recovery
            and not self._dirty
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
                    self._dirty |= changed
                    if pending.stop_requested or self._storage_unknown:
                        # Reattempt a failed persistence before each idempotent
                        # stop retry; stores suppress unchanged disk writes.
                        try:
                            await self._save_state()
                        except (OSError, RuntimeError, ValueError) as err:
                            # Disk trouble must not leave an already reached
                            # goal charging. A failed save can leave either
                            # snapshot on disk, so restart state is uncertain.
                            persistence_error = err
                            if isinstance(err, StorageStateUnknownError):
                                self._storage_unknown = True
                        if pending.stop_requested or self._storage_unknown:
                            self._control.pause_requested = True
                    elif self._dirty or pending.resume_on_recovery:
                        await self._save_state()
                    if (
                        pending.status == "active" and pending.resume_on_recovery
                        and await self._control.async_energy_goal_recovery_ready()
                    ):
                        # Consume the transient grant before a possible write.
                        # Resume follows the ordinary vehicle guard and is
                        # invalidated by later explicit goals or commands.
                        pending.resume_on_recovery = False
                        await self._save_state()
                        if not self._control.energy_goal_stop_requested:
                            await self._control.async_resume_for_energy_goal_locked(generation)
                    if changed:
                        self._notify_changed()
                if pending.stop_requested or self._storage_unknown:
                    await self._async_pause()
                if persistence_error is not None:
                    if isinstance(persistence_error, StorageStateUnknownError):
                        self._notice.failed("storage cannot be verified", level=logging.ERROR)
                        self._notify_changed()
                        return
                    raise persistence_error
                self._notice.recovered("enforced again")
        except StorageStateUnknownError:
            self._storage_unknown = True
            self._control.pause_requested = True
            self._notice.failed("storage cannot be verified", level=logging.ERROR)
            # A later poll retries the fenced stop after a command failure.
            with suppress(DeviceError, AmtronBusError, OSError, RuntimeError, ValueError):
                await self._async_pause()
            self._notify_changed()
        except (DeviceError, AmtronBusError, OSError, RuntimeError, ValueError) as err:
            self._notice.failed("cannot be enforced", type(err).__name__, level=logging.ERROR)
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
                _LOGGER.error("Energy target: unable to publish an update")

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
                self._dirty = True
            if self._dirty:
                try:
                    await self._save_state()
                except (OSError, RuntimeError, ValueError) as err:
                    _LOGGER.error("Energy target: stop not saved on shutdown (%s)", type(err).__name__)
