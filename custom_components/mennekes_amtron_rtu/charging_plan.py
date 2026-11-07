"""Bounded, side-effect-free calculations for a timed vehicle charge target."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MIN_CURRENT_A = 6.0
MAX_CAPACITY_KWH = 250.0
NOMINAL_VOLTAGE_V = 230.0
# Replanning from each fresh SoC report compensates for this initial estimate.
DEFAULT_BATTERY_EFFICIENCY = 0.9


def usable_capacity(value: object, unit: str = "kWh") -> float | None:
    """Accept a positive usable capacity only when its energy unit is known."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        amount = float(value)
    except OverflowError:
        return None
    if unit == "Wh":
        amount /= 1000
    elif unit != "kWh":
        return None
    return amount if math.isfinite(amount) and 0 < amount <= MAX_CAPACITY_KWH else None


@dataclass(frozen=True, slots=True)
class ChargePlan:
    """One proposed current and whether the target can fit the remaining time."""

    current_a: float | None
    status: str
    required_kwh: float
    start_at: datetime | None = None


def plan_current(
    *, now: datetime, deadline: datetime, soc_percent: float,
    target_percent: float, capacity_kwh: float, phases: int,
    max_current_a: float, efficiency: float = DEFAULT_BATTERY_EFFICIENCY,
) -> ChargePlan:
    """Recalculate a bounded current; never issue a bus command here."""
    if now.tzinfo is None or deadline.tzinfo is None or now.utcoffset() is None or deadline.utcoffset() is None:
        raise ValueError("charge deadline needs an aware timestamp")
    if (
        not all(math.isfinite(value) for value in (
            soc_percent, target_percent, capacity_kwh, max_current_a, efficiency,
        ))
        or not 0 <= soc_percent <= 100
        or not 0 < target_percent <= 100
        or usable_capacity(capacity_kwh) is None
        or phases not in (1, 2, 3)
        or max_current_a < MIN_CURRENT_A
        or not 0 < efficiency <= 1
    ):
        raise ValueError("invalid charge-plan input")
    required = max(0.0, (target_percent - soc_percent) * capacity_kwh / 100)
    if required == 0:
        return ChargePlan(None, "reached", 0.0)
    # Python subtracts equal tzinfo objects as wall-clock time across DST.
    now, deadline = now.astimezone(UTC), deadline.astimezone(UTC)
    hours = (deadline - now).total_seconds() / 3600
    if hours <= 0:
        return ChargePlan(max_current_a, "late", required)
    amps = required * 1000 / (hours * NOMINAL_VOLTAGE_V * phases * efficiency)
    if amps > max_current_a:
        return ChargePlan(max_current_a, "unreachable", required)
    if amps < MIN_CURRENT_A:
        minimum_hours = required * 1000 / (
            MIN_CURRENT_A * NOMINAL_VOLTAGE_V * phases * efficiency
        )
        return ChargePlan(
            MIN_CURRENT_A, "early_at_minimum", required,
            deadline - timedelta(hours=minimum_hours),
        )
    return ChargePlan(min(max_current_a, math.ceil(amps * 10) / 10), "paced", required)
