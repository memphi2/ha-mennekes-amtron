"""Durable additional-energy goals, independent of transports and Home Assistant.

An owner arms a goal with a fresh lifetime-meter reading and feeds subsequent
fresh readings to ``observe``. Cached values must not be passed as fresh. If a
measurement is missing, invalid or stale, the owner supplies ``None`` or
``fresh=False``. That requests a pause instead of estimating energy. Fresh valid readings can
recover a missing meter; completion and counter resets remain latched.

This module never writes to the wallbox. The owner attempts to persist a changed
goal before acting on its stop request and separately confirms the actual pause.
Storage failure must not prevent a stop whose energy limit is already known.
Normal meter progress is transient and does not require storage or Recorder
updates. Restored goals retain their original meter baseline and stop latch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

MIN_ENERGY_GOAL_KWH: Final = 0.1
MAX_ENERGY_GOAL_KWH: Final = 1000.0
ENERGY_GOAL_STEP_KWH: Final = 0.1
ENERGY_GOAL_STATES: Final = (
    "disabled", "active", "reached", "meter_unavailable", "meter_reset",
)
_STOP_STATES = frozenset({"reached", "meter_unavailable", "meter_reset"})
_SNAPSHOT_KEYS = frozenset({"target_kwh", "baseline_kwh", "status"})
MAX_COST_GOAL: Final = 1000.0
MIN_COST_GOAL: Final = 0.1
COST_GOAL_STEP: Final = 0.1
_COST_KEYS = frozenset({"amount", "spent", "price", "booked_kwh"})
# A smaller price change does not re-arm the amount (0.1 ct/kWh).
PRICE_TOLERANCE: Final = 0.001
# A price that wobbles every few seconds must not re-arm and save the
# target every few seconds.
PRICE_INTERVAL_SECONDS: Final = 60.0
# No real tariff comes near; it keeps every booking finite.
MAX_PRICE: Final = 100.0

type EnergyGoalSnapshot = dict[str, float | str | dict[str, float | str] | None]


def finite_number(value: object) -> float | None:
    """A real finite number as float, else None (never bool or huge ints)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def checked_currency(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 3 or not (value.isascii() and value.isalpha() and value.isupper()):
        raise ValueError("currency must be a three-letter code")
    return value


@dataclass(frozen=True, slots=True)
class CostPart:
    """The amount of money an energy target stands for.

    ``spent`` is what the first ``booked_kwh`` above the target's baseline
    cost; beyond them ``price`` applies. A negative price earns money back.
    """

    amount: float
    spent: float
    price: float
    booked_kwh: float = 0.0
    currency: str | None = None

    def __post_init__(self) -> None:
        if any(finite_number(value) is None for value in (self.amount, self.spent, self.price, self.booked_kwh)):
            raise ValueError("cost values must be finite numbers")
        if not MIN_COST_GOAL <= self.amount <= MAX_COST_GOAL:
            raise ValueError("cost target must be between 0.1 and 1000")
        if self.booked_kwh < 0:
            raise ValueError("cost booked energy must not be negative")

    def snapshot(self) -> dict[str, float | str]:
        result: dict[str, float | str] = {"amount": self.amount, "spent": self.spent, "price": self.price, "booked_kwh": self.booked_kwh}
        if self.currency is not None:
            result["currency"] = self.currency
        return result

    @classmethod
    def restore(cls, raw: object) -> CostPart:
        if not isinstance(raw, dict) or raw.keys() - {"currency"} != _COST_KEYS:
            raise ValueError("invalid cost target fields")
        return cls(raw["amount"], raw["spent"], raw["price"], raw["booked_kwh"], checked_currency(raw.get("currency")))


def energy_for(amount: float, price: float) -> float:
    """The energy that keeps within ``amount`` at ``price``.

    Rounded down to the target's step, so the amount is not exceeded; at
    least one step. A price of zero or below never uses the amount up.
    """
    ratio = amount / price / ENERGY_GOAL_STEP_KWH if price > 0 else math.inf
    if not math.isfinite(ratio):
        return MAX_ENERGY_GOAL_KWH
    steps = math.floor(ratio + 1e-9)
    return min(MAX_ENERGY_GOAL_KWH, max(MIN_ENERGY_GOAL_KWH, steps / 10))


def _nonnegative_number(value: object) -> float:
    """Accept actual finite numbers, never bool, strings or overflowing ints."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("energy value must be a finite non-negative number")
    try:
        number = float(value)
    except OverflowError as err:
        raise ValueError("energy value must be finite") from err
    if not math.isfinite(number) or number < 0:
        raise ValueError("energy value must be a finite non-negative number")
    return number


def _target(value: object) -> float:
    target = _nonnegative_number(value)
    if target == 0:
        return 0.0
    if not MIN_ENERGY_GOAL_KWH <= target <= MAX_ENERGY_GOAL_KWH:
        raise ValueError("energy goal must be 0 or between 0.1 and 1000 kWh")
    steps = target / ENERGY_GOAL_STEP_KWH
    if not math.isclose(steps, round(steps), rel_tol=0, abs_tol=1e-8):
        raise ValueError("energy goal must use 0.1 kWh steps")
    return round(steps) / 10


def _baseline(value: object, target: float) -> float:
    baseline = _nonnegative_number(value)
    # A corrupt/absurdly large counter must not make an addition round back to
    # the baseline and immediately mark a newly armed goal as reached.
    threshold = baseline + target
    if not math.isfinite(threshold) or threshold <= baseline:
        raise ValueError("energy meter cannot represent this goal")
    return baseline


class EnergyGoal:
    """Keep a ceiling, permanent completion/reset latches and a recoverable outage."""

    def __init__(self) -> None:
        self._target_kwh = 0.0
        self._baseline_kwh: float | None = None
        self._status = "disabled"
        self._last_total_kwh: float | None = None
        # Set when the target stands for an amount of money.
        self.cost: CostPart | None = None
        # Only this running owner may undo a pause it caused. A disk failure
        # must never resurrect an old resume permission after a manual pause.
        self.resume_on_recovery = False

    @property
    def target_kwh(self) -> float:
        """Return the requested additional energy, or zero when disabled."""
        return self._target_kwh

    @property
    def baseline_kwh(self) -> float | None:
        """Return the fixed lifetime-meter anchor for this goal."""
        return self._baseline_kwh

    @property
    def status(self) -> str:
        """Return a stable goal state, independent of physical charging state."""
        return self._status

    @property
    def stop_requested(self) -> bool:
        """Return whether the owner must pause and keep the stop intent."""
        return self._status in _STOP_STATES

    def set_target(self, target_kwh: object, total_kwh: object = None) -> bool:
        """Set an additional-energy goal; zero disables without needing a meter.

        Every explicit positive request establishes a new baseline. It never
        authorizes a start/resume or changes a deliberate pause. Validation
        happens before any state changes, and this core performs no commands.
        Return whether the durable snapshot changed and needs saving.
        """
        target = _target(target_kwh)
        baseline = _baseline(total_kwh, target) if target else None
        previous = self.snapshot()
        self._target_kwh = target
        self._baseline_kwh = self._last_total_kwh = baseline
        self._status = "active" if target else "disabled"
        self.resume_on_recovery = False
        self.cost = None
        return previous != self.snapshot()

    def observe(self, total_kwh: object, *, fresh: bool = True) -> bool:
        """Latch a terminal result; return true only for a durable transition.

        Call this only for a new measurement or a known failure/staleness.
        Merely skipping a meter read because it is not due is not a failure.
        A falling counter also invalidates a goal when still above its baseline.
        """
        if self._status not in ("active", "meter_unavailable"):
            return False
        previous = self.snapshot()
        try:
            total = _nonnegative_number(total_kwh)
        except ValueError:
            total = None
        if not fresh or total is None:
            self._status = "meter_unavailable"
        elif self._last_total_kwh is not None and total < self._last_total_kwh:
            self._status = "meter_reset"
        elif self._baseline_kwh is not None and total >= self._baseline_kwh + self._target_kwh:
            self._status = "reached"
            # The last reading counts, and the money it used is kept with the
            # goal, so it shows in full and survives a restart.
            self._last_total_kwh = total
            if self.cost is not None:
                self.cost = self._booked(self.cost, self.cost.price)
        else:
            self._last_total_kwh = total
            self._status = "active"
        if self._status in ("reached", "meter_reset"):
            self.resume_on_recovery = False
        return self.snapshot() != previous

    def _delivered(self, cost: CostPart) -> float:
        """Energy above the baseline at the last poll, never below the booked."""
        # An active goal always has both; the fallback only satisfies typing.
        polled = 0.0 if self._baseline_kwh is None or self._last_total_kwh is None else (
            self._last_total_kwh - self._baseline_kwh
        )
        return max(cost.booked_kwh, polled)

    @property
    def cost_spent(self) -> float | None:
        """Money used so far: the booked part plus the energy since, at the price."""
        cost = self.cost
        if cost is None:
            return None
        return cost.spent + (self._delivered(cost) - cost.booked_kwh) * cost.price

    def progress(self, total_kwh: object) -> float | None:
        """How much of the running goal is used, 0 to 1, at this meter reading.

        Energy above the baseline against the target, or for an amount of
        money what it cost so far against the amount. A reached goal is
        used up and a running one stays below it; without a goal or a usable
        reading there is none.
        """
        if self._status == "reached":
            return 1.0
        baseline = self._baseline_kwh
        total = finite_number(total_kwh)
        if self._status != "active" or baseline is None or total is None or total < baseline:
            return None
        delivered = total - baseline
        cost = self.cost
        if cost is None:
            share = delivered / self._target_kwh
        else:
            spent = cost.spent + (max(cost.booked_kwh, delivered) - cost.booked_kwh) * cost.price
            share = spent / cost.amount
        # Used up only once the goal latched: a running one stays below.
        return min(0.99, max(0.0, share))

    def take_cost(self, other: EnergyGoal) -> None:
        """Adopt the repriced target and amount of the same goal's copy."""
        self._target_kwh = other._target_kwh
        self.cost = other.cost

    def reprice(self, price: float) -> bool:
        """Book the energy so far at the old price and aim the rest at ``price``.

        Only a running amount changes, and only its target and amount: the
        baseline, the status and every latch stay, so this is no new goal.
        """
        cost = self.cost
        if cost is None or self._status != "active":
            return False
        booked = self._booked(cost, price)
        # With less than one step of money left the rest is one step.
        steps = math.floor(
            (booked.booked_kwh + energy_for(cost.amount - booked.spent, price)) / ENERGY_GOAL_STEP_KWH + 1e-9,
        )
        self._target_kwh = min(MAX_ENERGY_GOAL_KWH, max(MIN_ENERGY_GOAL_KWH, steps / 10))
        self.cost = booked
        return True

    def _booked(self, cost: CostPart, price: float) -> CostPart:
        """The energy so far booked at the running price; ``price`` applies beyond."""
        delivered = self._delivered(cost)
        return CostPart(cost.amount, cost.spent + (delivered - cost.booked_kwh) * cost.price, price, delivered, cost.currency)

    def snapshot(self) -> EnergyGoalSnapshot:
        """Return bounded durable state, excluding ordinary meter progress."""
        snapshot: EnergyGoalSnapshot = {
            "target_kwh": self._target_kwh,
            "baseline_kwh": self._baseline_kwh,
            "status": self._status,
        }
        if self.cost is not None:
            snapshot["cost"] = self.cost.snapshot()
        return snapshot

    @classmethod
    def restore(cls, raw: object) -> EnergyGoal:
        """Restore exact stored state; malformed state must not silently disable it."""
        if not isinstance(raw, dict) or raw.keys() - {"cost"} != _SNAPSHOT_KEYS:
            raise ValueError("invalid energy goal state fields")
        cost = CostPart.restore(raw["cost"]) if "cost" in raw else None
        target = _target(raw["target_kwh"])
        status = raw["status"]
        if not isinstance(status, str) or status not in ENERGY_GOAL_STATES:
            raise ValueError("invalid energy goal status")
        if target == 0:
            if status != "disabled" or raw["baseline_kwh"] is not None or cost is not None:
                raise ValueError("disabled energy goal has an active baseline or status")
            return cls()
        if status == "disabled":
            raise ValueError("active energy goal cannot be disabled")
        baseline = _baseline(raw["baseline_kwh"], target)
        goal = cls()
        goal._target_kwh = target
        goal._baseline_kwh = goal._last_total_kwh = baseline
        goal._status = status
        goal.cost = cost
        return goal
