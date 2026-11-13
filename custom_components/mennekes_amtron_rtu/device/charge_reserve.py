"""An optional, meter-measured early portion of the existing energy ceiling."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .energy_goal import EnergyGoal


def reserve_energy(value: object, ceiling: float) -> float:
    """Zero disables; positive reserve uses the ordinary 0.1 kWh bounds."""
    goal = EnergyGoal()
    goal.set_target(value, 0.0)
    if goal.target_kwh > ceiling:
        raise ValueError("reserve exceeds the job energy ceiling")
    return goal.target_kwh


@dataclass(slots=True)
class ChargeReserve:
    """A reached reserve cannot rearm, even if later samples fall or disappear.

    This is additional AC energy from the order's existing meter baseline,
    not a battery percentage or a guarantee of usable driving energy.
    """

    target_kwh: float
    reached: bool = False

    @classmethod
    def create(cls, value: object, *, ceiling: float, kind: str, baseline: object = 0.0) -> ChargeReserve | None:
        target = reserve_energy(value, ceiling)
        if target and kind != "scheduled":
            raise ValueError("reserve is available only for scheduled jobs")
        if target:
            meter_check = EnergyGoal()
            meter_check.set_target(target, baseline)
        return cls(target) if target else None

    def needed(self, total: object, baseline: float | None) -> bool:
        """A missing or invalid meter cannot authorize early charging."""
        if self.reached or baseline is None or isinstance(total, bool) or not isinstance(total, (int, float)):
            return False
        try:
            reading = float(total)
        except OverflowError:
            return False
        return math.isfinite(reading) and baseline <= reading < baseline + self.target_kwh

    def observe(self, energy: EnergyGoal, total: object, *, fresh: bool) -> None:
        # EnergyGoal has already validated this same sample and checked reset.
        if fresh and energy.status in ("active", "reached") and not self.needed(total, energy.baseline_kwh):
            self.reached = True

    def snapshot(self) -> dict[str, float | bool]:
        return {"energy_kwh": self.target_kwh, "reached": self.reached}

    @classmethod
    def restore(cls, raw: object, *, ceiling: float, kind: str, energy_status: str, baseline: object = 0.0) -> ChargeReserve:
        if not isinstance(raw, dict) or raw.keys() != {"energy_kwh", "reached"} or type(raw["reached"]) is not bool:
            raise ValueError("invalid saved reserve")
        reserve = cls.create(raw["energy_kwh"], ceiling=ceiling, kind=kind, baseline=baseline)
        if reserve is None or (energy_status == "reached" and raw["reached"] is not True):
            raise ValueError("inconsistent saved reserve")
        reserve.reached = raw["reached"]
        return reserve
