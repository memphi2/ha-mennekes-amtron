"""Bounded, side-effect-free calculations for a timed vehicle charge target."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .const import CHARGING_CURRENT_MINIMUM

MAX_CAPACITY_KWH = 250.0
NOMINAL_VOLTAGE_V = 230.0
# Replanning from each fresh SoC report compensates for this initial estimate.
DEFAULT_BATTERY_EFFICIENCY = 0.9
# Time a late start keeps for a charge slower than estimated; the minimum
# current at the end uses up what is left of it.
START_RESERVE = timedelta(minutes=30)
# Match the observer's five-minute write floor: do not send a full-current
# pulse that would cross the finishing boundary before it can be reduced.
FINISHING_WINDOW_HOURS = 5 / 60
_TIME_TOLERANCE_HOURS = 1e-6  # Less than four milliseconds of date/float rounding.
# A metered full-current power counts only this close to the nominal one.
_POWER_LOW, _POWER_HIGH = 0.3, 1.1


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
    # Hours the rest of the charge takes at full current.
    full_hours: float | None = None


def plan_current(
    *, now: datetime, deadline: datetime, soc_percent: float,
    target_percent: float, capacity_kwh: float, phases: int,
    max_current_a: float, efficiency: float = DEFAULT_BATTERY_EFFICIENCY,
    full_power_kw: float | None = None,
) -> ChargePlan:
    """Start late at full current and land on the target at departure.

    The start leaves a reserve for a slower charge than estimated. Charging
    stays at full current until the finishing boundary enters the next
    five-minute control window. An intermediate current then approaches the
    minimum without a full-current overshoot. Each fresh SoC report
    recalculates this; never issue a bus command.

    ``full_power_kw`` is the power the car was metered to draw at full
    current. It replaces the nominal figure within bounds; a car that draws
    less than offered then also counts less per ampere, which starts earlier.
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
        or (full_power_kw is not None and not (math.isfinite(full_power_kw) and full_power_kw > 0))
    ):
        raise ValueError("invalid charge-plan input")
    required = max(0.0, (target_percent - soc_percent) * capacity_kwh / 100)
    if required == 0:
        return ChargePlan(None, "reached")
    # Python subtracts equal tzinfo objects as wall-clock time across DST.
    now, deadline = now.astimezone(UTC), deadline.astimezone(UTC)
    hours = (deadline - now).total_seconds() / 3600
    kw_per_amp = NOMINAL_VOLTAGE_V * phases * efficiency / 1000
    if full_power_kw is not None:
        nominal = max_current_a * NOMINAL_VOLTAGE_V * phases / 1000
        measured = min(_POWER_HIGH * nominal, max(_POWER_LOW * nominal, full_power_kw))
        kw_per_amp = min(kw_per_amp, measured * efficiency / max_current_a)
        full_hours = required / (measured * efficiency)
    else:
        full_hours = required / (max_current_a * kw_per_amp)
    if hours <= 0:
        return ChargePlan(max_current_a, "late", full_hours=full_hours)
    if full_hours > hours + _TIME_TOLERANCE_HOURS:
        return ChargePlan(max_current_a, "unreachable", full_hours=full_hours)
    minimum_hours = required / (CHARGING_CURRENT_MINIMUM * kw_per_amp)
    start_at = deadline - min(timedelta(hours=minimum_hours), timedelta(hours=full_hours) + START_RESERVE)
    if minimum_hours <= hours + _TIME_TOLERANCE_HOURS:
        return ChargePlan(CHARGING_CURRENT_MINIMUM, "finishing", start_at, full_hours)
    # Consume only the energy above the minimum-current trajectory in the
    # next control window. In the final partial window, aim at the deadline
    # itself. Whole-amp upward steps match the observer's 1 A change threshold
    # and avoid understating the remaining demand; the configured cap wins.
    window = min(hours, FINISHING_WINDOW_HOURS)
    surplus_amp_hours = CHARGING_CURRENT_MINIMUM * (minimum_hours - hours)
    current = min(max_current_a, math.ceil(CHARGING_CURRENT_MINIMUM + surplus_amp_hours / window))
    return ChargePlan(current, "charging", start_at, full_hours)
