"""A bounded log of total-energy readings, to measure any past time span.

A vehicle reports its own charging span; the energy the wallbox delivered in
that span is the difference of two meter readings. Between two readings the
meter is taken to rise evenly, which only matters for a span that begins or
ends while charging runs.
"""

from __future__ import annotations

import math
from bisect import bisect_right

_MAX_READINGS = 4096
# Two readings further apart than this, with energy between them, cannot
# place a boundary: nobody saw when that energy flowed.
MAX_INTERPOLATION_SECONDS = 600.0


class MeterLog:
    """Readings in time order; an unchanged meter keeps only its first and last."""

    def __init__(self) -> None:
        self._times: list[float] = []
        self._values: list[float] = []

    @property
    def first_at(self) -> float | None:
        return self._times[0] if self._times else None

    def observe(self, at: float, kwh: float) -> None:
        if not (math.isfinite(at) and math.isfinite(kwh)) or kwh < 0:
            return
        if self._times and (at <= self._times[-1] or kwh < self._values[-1]):
            if kwh < self._values[-1] or at < self._times[-1]:
                # A replaced meter or a clock step: older readings no longer compare.
                self._times, self._values = [at], [kwh]
            return
        if len(self._values) >= 2 and self._values[-1] == self._values[-2] == kwh:
            self._times[-1] = at
            return
        self._times.append(at)
        self._values.append(kwh)
        if len(self._times) > _MAX_READINGS:
            del self._times[0], self._values[0]

    def _at(self, moment: float) -> float | None:
        index = bisect_right(self._times, moment)
        if index == 0:
            return None
        if self._times[index - 1] == moment:
            return self._values[index - 1]
        if index == len(self._times):
            return None
        before, after = self._values[index - 1], self._values[index]
        if before == after:
            return before
        earlier, later = self._times[index - 1], self._times[index]
        if later - earlier > MAX_INTERPOLATION_SECONDS:
            return None
        return before + (after - before) * (moment - earlier) / (later - earlier)

    def energy_between(self, start: float, end: float) -> float | None:
        """Energy delivered from start to end; None unless both are covered."""
        if not start < end:
            return None
        first, last = self._at(start), self._at(end)
        if first is None or last is None:
            return None
        return last - first
