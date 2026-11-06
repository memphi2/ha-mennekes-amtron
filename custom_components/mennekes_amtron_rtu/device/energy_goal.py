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
from typing import Final

MIN_ENERGY_GOAL_KWH: Final = 0.1
MAX_ENERGY_GOAL_KWH: Final = 1000.0
ENERGY_GOAL_STEP_KWH: Final = 0.1
ENERGY_GOAL_STATES: Final = (
    "disabled", "active", "reached", "meter_unavailable", "meter_reset",
)
_STOP_STATES = frozenset({"reached", "meter_unavailable", "meter_reset"})
_SNAPSHOT_KEYS = frozenset({"target_kwh", "baseline_kwh", "status"})

type EnergyGoalSnapshot = dict[str, float | str | None]


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
        else:
            self._last_total_kwh = total
            self._status = "active"
        if self._status in ("reached", "meter_reset"):
            self.resume_on_recovery = False
        return self.snapshot() != previous

    def snapshot(self) -> EnergyGoalSnapshot:
        """Return bounded durable state, excluding ordinary meter progress."""
        return {
            "target_kwh": self._target_kwh,
            "baseline_kwh": self._baseline_kwh,
            "status": self._status,
        }

    @classmethod
    def restore(cls, raw: object) -> EnergyGoal:
        """Restore exact stored state; malformed state must not silently disable it."""
        if not isinstance(raw, dict) or raw.keys() != _SNAPSHOT_KEYS:
            raise ValueError("invalid energy goal state fields")
        target = _target(raw["target_kwh"])
        status = raw["status"]
        if not isinstance(status, str) or status not in ENERGY_GOAL_STATES:
            raise ValueError("invalid energy goal status")
        if target == 0:
            if status != "disabled" or raw["baseline_kwh"] is not None:
                raise ValueError("disabled energy goal has an active baseline or status")
            return cls()
        if status == "disabled":
            raise ValueError("active energy goal cannot be disabled")
        baseline = _baseline(raw["baseline_kwh"], target)
        goal = cls()
        goal._target_kwh = target
        goal._baseline_kwh = goal._last_total_kwh = baseline
        goal._status = status
        return goal
