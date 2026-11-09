"""Bounded, side-effect-free calculations for a timed vehicle charge target."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .device.const import CHARGING_CURRENT_MINIMUM

MAX_CAPACITY_KWH = 250.0
NOMINAL_VOLTAGE_V = 230.0
# Replanning from each fresh SoC report compensates for this initial estimate.
DEFAULT_BATTERY_EFFICIENCY = 0.9
# Time a late start keeps for a charge slower than estimated; the minimum
# current at the end uses up what is left of it.
START_RESERVE = timedelta(minutes=30)


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
    """One proposed current and when charging should begin."""

    current_a: float | None
    status: str
    start_at: datetime | None = None


def plan_current(
    *, now: datetime, deadline: datetime, soc_percent: float,
    target_percent: float, capacity_kwh: float, phases: int,
    max_current_a: float, efficiency: float = DEFAULT_BATTERY_EFFICIENCY,
) -> ChargePlan:
    """Start late at full current and land on the target at departure.

    The start leaves a reserve for a slower charge than estimated. Charging
    stays at full current until the remaining energy just fits the rest of the
    time at the minimum current, and then finishes at that minimum instead of
    early. Each fresh SoC report recalculates this; never issue a bus command.
    """
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
        or max_current_a < CHARGING_CURRENT_MINIMUM
        or not 0 < efficiency <= 1
    ):
        raise ValueError("invalid charge-plan input")
    required = max(0.0, (target_percent - soc_percent) * capacity_kwh / 100)
    if required == 0:
        return ChargePlan(None, "reached")
    # Python subtracts equal tzinfo objects as wall-clock time across DST.
    now, deadline = now.astimezone(UTC), deadline.astimezone(UTC)
    hours = (deadline - now).total_seconds() / 3600
    if hours <= 0:
        return ChargePlan(max_current_a, "late")
    kw_per_amp = NOMINAL_VOLTAGE_V * phases * efficiency / 1000
    full_hours = required / (max_current_a * kw_per_amp)
    if full_hours > hours:
        return ChargePlan(max_current_a, "unreachable")
    minimum_hours = required / (CHARGING_CURRENT_MINIMUM * kw_per_amp)
    start_at = deadline - min(timedelta(hours=minimum_hours), timedelta(hours=full_hours) + START_RESERVE)
    if minimum_hours >= hours:
        return ChargePlan(max_current_a, "charging", start_at)
    return ChargePlan(CHARGING_CURRENT_MINIMUM, "finishing", start_at)
