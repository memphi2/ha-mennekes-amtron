"""A vehicle-reported percentage ceiling, without guessing battery capacity."""

from __future__ import annotations

import math

SOC_GOAL_STATES = ("disabled", "active", "reached")
_KEYS = {"source", "target_percent", "maximum_percent", "status"}


def percentage(value: object, *, maximum: bool = False) -> float:
    """Validate an actual finite percentage; zero is not a useful car cap."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("percentage must be a number")
    try:
        result = float(value)
    except OverflowError as err:
        raise ValueError("percentage must be finite") from err
    if not math.isfinite(result) or not 0 <= result <= 100 or (maximum and result == 0):
        raise ValueError("percentage is out of range")
    return result


class SocGoal:
    """Keep an optional ceiling bound to the selected vehicle sources.

    Missing vehicle data deliberately leaves charging unchanged. A reached
    ceiling stays reached until an explicit new goal, disable or source change,
    or until a target at the car's limit follows a raised limit that the
    current charge level has not reached. The last valid car cap remains in force when its
    entity becomes unavailable. None of these operations authorizes starting
    or resuming charging. A reached target at the car's own limit pauses
    nothing: the car ends that charge itself, like at any other charger.
    """

    def __init__(self) -> None:
        self.source: str | None = None
        self.target_percent = 0.0
        self.maximum_percent: float | None = None
        self.status = "disabled"

    @property
    def stop_requested(self) -> bool:
        return self.status == "reached" and self.target_percent != self.maximum_percent

    def configure_source(self, source: str | None) -> bool:
        """Forget the old car's ceiling and cap when its sources change."""
        if source is not None and (
            not isinstance(source, str) or not source or len(source) > 512
        ):
            raise ValueError("invalid vehicle source")
        if source == self.source:
            return False
        self.source = source
        self.target_percent = 0.0
        self.maximum_percent = None
        self.status = "disabled"
        return True

    def set_target(
        self, value: object, soc: object = None, maximum: object = None,
    ) -> bool:
        """Set a ceiling using the current vehicle data; zero only disables."""
        target = percentage(value)
        if target == 0:
            previous = self.snapshot()
            self.target_percent = 0.0
            self.status = "disabled"
            return previous != self.snapshot()
        cap = self.maximum_percent if maximum is None else percentage(maximum, maximum=True)
        current = None if soc is None else percentage(soc)
        if target and self.source is None:
            raise ValueError("vehicle source is not configured")
        if target and cap is not None and target > cap:
            raise ValueError("target exceeds the vehicle charge limit")
        previous = self.snapshot()
        self.target_percent = target
        self.maximum_percent = cap
        self.status = "active" if target else "disabled"
        if target and current is not None and current >= target:
            self.status = "reached"
        return previous != self.snapshot()

    def observe(self, soc: object, maximum: object = None) -> bool:
        """Apply valid inputs, retaining prior evidence when a source is missing."""
        previous = self.snapshot()
        try:
            cap = percentage(maximum, maximum=True)
        except ValueError:
            cap = None
        try:
            current = percentage(soc)
        except ValueError:
            current = None
        if cap is not None and self.source is not None:
            # A target at the car's limit follows that limit both ways; any
            # other target only ever drops to a lower limit.
            following = bool(self.target_percent) and self.target_percent == self.maximum_percent
            self.maximum_percent = cap
            if following:
                if cap > self.target_percent and self.status == "reached":
                    # Only the latch ends; a known charge level at or above
                    # the new target latches again below, and resuming the
                    # stop pause stays manual.
                    self.status = "active"
                self.target_percent = cap
            elif self.target_percent:
                self.target_percent = min(self.target_percent, cap)
        if self.status == "active" and current is not None and current >= self.target_percent:
            self.status = "reached"
        return previous != self.snapshot()

    def snapshot(self) -> dict[str, float | str | None]:
        """Persist only configuration and the latch, never ordinary SoC updates."""
        return {
            "source": self.source,
            "target_percent": self.target_percent,
            "maximum_percent": self.maximum_percent,
            "status": self.status,
        }

    @classmethod
    def restore(cls, raw: object) -> SocGoal:
        if not isinstance(raw, dict) or raw.keys() != _KEYS:
            raise ValueError("invalid percentage goal fields")
        goal = cls()
        goal.configure_source(raw["source"])
        target = percentage(raw["target_percent"])
        cap = raw["maximum_percent"]
        if cap is not None:
            cap = percentage(cap, maximum=True)
        status = raw["status"]
        if (
            not isinstance(status, str) or status not in SOC_GOAL_STATES
            or (target == 0) != (status == "disabled")
            or (goal.source is None and (target != 0 or cap is not None))
            or (cap is not None and target > cap)
        ):
            raise ValueError("inconsistent percentage goal")
        goal.target_percent = target
        goal.maximum_percent = cap
        goal.status = status
        return goal
