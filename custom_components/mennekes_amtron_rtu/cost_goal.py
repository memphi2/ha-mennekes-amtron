"""A charge target as an amount of money, carried by the service's energy target.

Home Assistant knows the price, the service watches the meter. The amount
still open is therefore handed to the service as additional energy at the
price that applies now, and handed over again whenever that price changes.
The service stops the charge on its own, also while Home Assistant is down;
it then stops at the energy of the last known price.

Between two hand-overs the price is constant by construction, so the money
spent is the energy since the service's baseline times that price. Nothing
else has to be measured or remembered here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .device.client_errors import AmtronBusError
from .device.data import WallboxData
from .device.energy_goal import (
    ENERGY_GOAL_STEP_KWH,
    MAX_ENERGY_GOAL_KWH,
    MIN_ENERGY_GOAL_KWH,
)
from .device.notice import FailureNotice
from .device.registers import ENERGY_TOTAL
from .exceptions import service_validation_error
from .goal_store import ModelStore

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)
MAX_COST_GOAL: Final = 1000.0
MIN_COST_GOAL: Final = 0.1
COST_GOAL_STEP: Final = 0.1
_RETRY_SECONDS: Final = 60.0
# Each hand-over saves the service's state and reads its meter. A price that
# wobbles every few seconds must not turn into a write every few seconds.
_HANDOVER_SECONDS: Final = 60.0
_PRICE_TOLERANCE: Final = 0.001
_KEYS = frozenset({"target", "spent", "price", "energy_kwh"})


def _amount(value: object, *, minimum: float = 0.0, maximum: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("amount must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("amount out of range")
    return number


def energy_for(amount: float, price: float) -> float:
    """The energy target that keeps a charge within ``amount`` at ``price``.

    Rounded down to the service's step, so the amount is not exceeded. A
    price of zero or below never uses the amount up.
    """
    if price <= 0:
        return MAX_ENERGY_GOAL_KWH
    steps = math.floor(amount / price / ENERGY_GOAL_STEP_KWH + 1e-9)
    # The same rounding as the service, so both hold the identical number.
    return min(MAX_ENERGY_GOAL_KWH, max(MIN_ENERGY_GOAL_KWH, steps / 10))


class CostGoal:
    """The amount, what is spent of it, and what the service was last given."""

    def __init__(
        self, target: float = 0.0, spent: float = 0.0,
        price: float | None = None, energy_kwh: float | None = None,
    ) -> None:
        self.target = target
        self.spent = spent
        self.price = price
        self.energy_kwh = energy_kwh

    def snapshot(self) -> dict[str, Any]:
        return {
            "target": self.target, "spent": self.spent,
            "price": self.price, "energy_kwh": self.energy_kwh,
        }

    @classmethod
    def restore(cls, raw: object) -> CostGoal:
        if not isinstance(raw, dict) or raw.keys() != _KEYS:
            raise ValueError("invalid cost target storage")
        target = _amount(raw["target"], maximum=MAX_COST_GOAL)
        if target == 0:
            return cls()
        price = raw["price"]
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
            raise ValueError("invalid cost target price")
        return cls(
            # A negative price earns money, so the spent part may be negative.
            target, _amount(raw["spent"], minimum=-math.inf), float(price),
            _amount(raw["energy_kwh"], maximum=MAX_ENERGY_GOAL_KWH),
        )


class _Attempt(NamedTuple):
    """A hand-over whose answer did not come: the service may hold it."""

    energy_kwh: float
    price: float
    baseline_kwh: float


class CostGoalObserver:
    """Keeps the service's energy target in step with the amount and the price.

    The poll only decides; the hand-over runs as its own task, because a
    command refreshes the poll that would be waiting for it.
    """

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._store = ModelStore(
            hass, entry.entry_id, entry.runtime_data.identity.serial_number or entry.entry_id,
            kind="cost_goal", model=CostGoal,
        )
        self.goal = CostGoal()
        self._notice = FailureNotice(_LOGGER, "Cost target")
        self._storage_notice = FailureNotice(_LOGGER, "Cost target")
        # One change at a time: the user's amount and a hand-over.
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._attempt: _Attempt | None = None
        self._retry_at = 0.0
        self._handed_at = -math.inf
        self._dirty = False

    async def async_start(self) -> None:
        try:
            self.goal = await self._store.async_load()
        except (ValueError, OSError):
            # The energy target in the service stays as it is and still stops
            # the charge; only the amount it stood for is no longer known.
            _LOGGER.warning("Cost target: saved amount unusable (the energy target in the service stays)")

    async def async_stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._dirty:
            await self._save()

    @property
    def target(self) -> float:
        return self.goal.target

    @property
    def status(self) -> str:
        """``disabled``, ``price_unknown`` or the state of the energy target."""
        if not self.goal.target:
            return "disabled"
        status = self._entry.runtime_data.control.energy_goal.status
        if status == "active" and self._price() is None:
            return "price_unknown"
        return status

    def _price(self) -> float | None:
        cost = self._entry.runtime_data.charging_cost
        return None if cost is None else cost.unit_price(time.time())

    def _spent_until(self, baseline: float | None, *, since: float | None, price: float | None) -> float:
        """What the interval from ``since`` up to ``baseline`` cost at ``price``."""
        if baseline is None or since is None or price is None:
            return self.goal.spent
        return self.goal.spent + max(0.0, baseline - since) * price

    async def async_set_target(self, value: float) -> None:
        """Arm a new amount from now on; zero ends it with its energy target."""
        try:
            target = _amount(value, maximum=MAX_COST_GOAL)
            if target and target < MIN_COST_GOAL:
                raise ValueError("amount below the minimum")
        except ValueError as err:
            raise service_validation_error("cost_goal_invalid") from err
        control = self._entry.runtime_data.control
        async with self._lock:
            self._attempt = None
            if not target:
                ours = self.goal.target and control.energy_goal.target_kwh == self.goal.energy_kwh
                if ours:
                    await control.async_set_energy_goal(0)
                self.goal = CostGoal()
                await self._save()
                return
            price = self._price()
            if price is None:
                raise service_validation_error("cost_goal_price_unknown")
            energy = energy_for(target, price)
            await control.async_set_energy_goal(energy)
            self._handed_at = time.monotonic()
            self.goal = CostGoal(target, 0.0, price, energy)
            await self._save()

    async def async_observe(self, data: WallboxData | None) -> None:
        """Follow the energy target, and start a hand-over at a new price."""
        if not self.goal.target or self._lock.locked():
            return
        if self._dirty and time.monotonic() >= self._retry_at:
            await self._save()
        if data is None:
            return
        energy_goal = self._entry.runtime_data.control.energy_goal
        attempt = self._attempt
        if attempt is not None and energy_goal.target_kwh == attempt.energy_kwh:
            # The hand-over whose answer was lost did reach the service.
            self._attempt = None
            self.goal = CostGoal(
                self.goal.target,
                self._spent_until(energy_goal.baseline_kwh, since=attempt.baseline_kwh, price=self.goal.price),
                attempt.price, attempt.energy_kwh,
            )
            await self._save()
        if energy_goal.target_kwh != self.goal.energy_kwh:
            # Somebody set or cleared the energy target itself: that is the
            # newer wish, and the amount no longer describes it.
            self._attempt = None
            self.goal = CostGoal()
            await self._save()
            return
        price = self._price()
        now = time.monotonic()
        goal = self.goal
        if (
            energy_goal.status != "active" or price is None or goal.price is None
            or abs(price - goal.price) < _PRICE_TOLERANCE
            or now < self._retry_at or now - self._handed_at < _HANDOVER_SECONDS
            or (self._task is not None and not self._task.done())
        ):
            return
        meter = data.get(ENERGY_TOTAL.key)
        baseline = energy_goal.baseline_kwh
        if (
            isinstance(meter, bool) or not isinstance(meter, (int, float))
            or not data.energy_read or baseline is None
        ):
            return
        left = goal.target - self._spent_until(float(meter), since=baseline, price=goal.price)
        if left < price * ENERGY_GOAL_STEP_KWH:
            # Not even one step is left: the target in force ends the charge.
            return
        self._handed_at = now
        self._task = self._entry.async_create_background_task(
            self._hass, self._hand_over(goal, price, energy_for(left, price), baseline),
            "mennekes_amtron_rtu_cost_goal",
        )

    async def _hand_over(self, goal: CostGoal, price: float, energy: float, baseline: float) -> None:
        control = self._entry.runtime_data.control
        async with self._lock:
            if self.goal is not goal or control.energy_goal.status != "active":
                return
            self._attempt = _Attempt(energy, price, baseline)
            try:
                await control.async_set_energy_goal(energy)
            except (AmtronBusError, HomeAssistantError):
                # The earlier energy target stays in force meanwhile, or the
                # command arrived and the next poll shows it.
                self._notice.failed("energy target not updated for the new price", "retrying")
                self._retry_at = time.monotonic() + _RETRY_SECONDS
                return
            self._attempt = None
            self._notice.recovered("energy target follows the price again")
            # The service read the meter itself for the new baseline.
            self.goal = CostGoal(
                goal.target,
                self._spent_until(control.energy_goal.baseline_kwh, since=baseline, price=goal.price),
                price, energy,
            )
            await self._save()

    async def _save(self) -> None:
        try:
            await self._store.async_save(self.goal)
        except (ValueError, OSError):
            self._dirty = True
            self._retry_at = time.monotonic() + _RETRY_SECONDS
            self._storage_notice.failed("amount not saved", "retrying")
        else:
            self._dirty = False
            self._storage_notice.recovered("amount saved again")
