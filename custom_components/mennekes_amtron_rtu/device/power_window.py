"""Bounded metered-power sampling shared by direct and service planning."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .departure_model import POWER_WINDOW_SECONDS
from .enums import GridPhasesConnected, SwitchedPhases


@dataclass(slots=True)
class PowerWindow:
    """One continuous full-current window; duplicate meter reads teach nothing."""

    start: tuple[float, float, int, float] | None = None
    vehicle: str | None = None

    def reset(self) -> None:
        self.start = None
        self.vehicle = None

    def sample(self, *, at: float, total: float, phases: int, current: float, vehicle: str) -> float | None:
        if not all(math.isfinite(value) for value in (at, total, current)) or total < 0:
            self.reset()
            return None
        previous = self.start
        sample = (at, total, phases, current)
        if (previous is None or self.vehicle != vehicle or previous[2:] != sample[2:]
                or at < previous[0] or total < previous[1]):
            self.start, self.vehicle = sample, vehicle
            return None
        seconds = at - previous[0]
        if seconds < POWER_WINDOW_SECONDS:
            return None
        self.start = sample
        return (total - previous[1]) * 3600 / seconds


def observed_phases(fresh: Callable[[str], object]) -> int | None:
    """Read the selected physical phase configuration, never a held fallback."""
    switched, grid, detected = (fresh(key) for key in (
        "switched_phases", "grid_phases_connected", "detected_ev_phases",
    ))
    if isinstance(switched, bool):
        return None
    if switched == SwitchedPhases.SINGLE_PHASE:
        return 1
    if switched != SwitchedPhases.ALL_AVAILABLE or isinstance(grid, bool):
        return None
    if grid == GridPhasesConnected.L1:
        return 1
    if (grid == GridPhasesConnected.L1_L2_L3 and not isinstance(detected, bool)
            and isinstance(detected, (int, float)) and detected in (1, 2, 3)):
        return int(detected)
    return None
