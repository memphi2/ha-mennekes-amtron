"""A bounded log of total-energy readings, to measure any past time span.

A vehicle reports its own charging span; the energy the wallbox delivered in
that span is the difference of two meter readings. Between two readings the
meter is taken to rise evenly, which only matters for a span that begins or
ends while charging runs.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from typing import Any

from .presence_gap import PresenceGap

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


def _moment(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid charge meter time")
    return float(value)


class ChargeMeter:
    """The meter log and the plug cycle the service saw from end to end.

    The service outlives a restart of Home Assistant, so what it measures
    here is whole where Home Assistant alone would have a gap.
    """

    def __init__(self) -> None:
        self.log = MeterLog()
        self.last: tuple[float, float] | None = None
        # Since when presence has been known without a long gap.
        self.known_since: float | None = None
        self._gap = PresenceGap()
        self._lose_cycle()

    def _lose_cycle(self) -> None:
        """Nobody saw the whole cycle any more: it cannot be measured."""
        self.present: bool | None = None
        self.plugged_at: float | None = None
        self.charging_at: float | None = None

    def observe(
        self, now: float, *, present: bool | None, charging: bool = False,
        reading: float | None = None,
    ) -> None:
        """One poll: whether a car is plugged in, and the meter if it was read."""
        if reading is not None:
            self.log.observe(now, reading)
        if self._gap.observe(now, known=present is not None):
            # A long gap may hide an unplug, and another car.
            self._lose_cycle()
            self.known_since = None
        if present is None:
            return
        if self.known_since is None:
            self.known_since = now
        if not present:
            if self.present and self.plugged_at is not None:
                self.last = (self.plugged_at, now)
            self._lose_cycle()
            self.present = False
            return
        if self.present is False:
            self.plugged_at = now
        self.present = True
        if charging and self.charging_at is None and self.plugged_at is not None:
            self.charging_at = now

    def snapshot(self) -> dict[str, Any]:
        return {
            "first_reading_at": self.log.first_at,
            "known_since": self.known_since,
            "present": self.present,
            "plugged_at": self.plugged_at,
            "charging_at": self.charging_at,
            "last": list(self.last) if self.last is not None else None,
        }


def parse_charge_meter(raw: object) -> dict[str, Any]:
    """Validate what a service reports of its charge meter."""
    if not isinstance(raw, dict) or raw.keys() != {
        "first_reading_at", "known_since", "present", "plugged_at", "charging_at", "last",
    } or (raw["present"] is not None and type(raw["present"]) is not bool):
        raise ValueError("invalid charge meter")
    last = raw["last"]
    if last is not None:
        if not isinstance(last, list) or len(last) != 2:
            raise ValueError("invalid plug cycle")
        start, end = _moment(last[0]), _moment(last[1])
        if start is None or end is None or not start < end:
            raise ValueError("invalid plug cycle")
        last = (start, end)
    return {
        "first_reading_at": _moment(raw["first_reading_at"]),
        "known_since": _moment(raw["known_since"]),
        "present": raw["present"],
        "plugged_at": _moment(raw["plugged_at"]),
        "charging_at": _moment(raw["charging_at"]),
        "last": last,
    }
