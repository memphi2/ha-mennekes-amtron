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

_MAX_READINGS = 2048
# A saved log longer than this is refused as damaged.
_MAX_STORED = 8192
# While the meter rises, one reading per this span is kept. Where a rise
# begins or ends, the readings stay exact.
_RISING_SPACING_SECONDS = 20.0
# The longest a coarse meter shows no step while a car charges at the least
# current; a rest that lasts longer is a rest, and thinning keeps its ends.
_STEP_SECONDS = 60.0
# Two readings further apart than this, with energy between them, cannot
# place a boundary: nobody saw when that energy flowed.
MAX_INTERPOLATION_SECONDS = 600.0


MAX_NOTE_LENGTH = 128


def _note(value: object) -> str | None:
    if value is not None and (not isinstance(value, str) or not 0 < len(value) <= MAX_NOTE_LENGTH):
        raise ValueError("invalid plug cycle note")
    return value


def moment(value: object) -> float:
    """A finite number as float; an integer too large for one is refused as well."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid charge meter time")
    try:
        number = float(value)
    except OverflowError as err:
        raise ValueError("invalid charge meter time") from err
    if not math.isfinite(number):
        raise ValueError("invalid charge meter time")
    return number


def _moment(value: object) -> float | None:
    return None if value is None else moment(value)


class MeterLog:
    """Readings in time order; an unchanged meter keeps only its first and last."""

    def __init__(self) -> None:
        self._times: list[float] = []
        self._values: list[float] = []
        # Known plug-ins and unplugs: the readings around them always stay.
        self.anchors: tuple[float, ...] = ()

    @property
    def last_kwh(self) -> float | None:
        return self._values[-1] if self._values else None

    def forget_before(self, moment: float) -> None:
        """Drop what lies before the last reading at or before that moment."""
        index = bisect_right(self._times, moment) - 1
        if index > 0:
            del self._times[:index], self._values[:index]

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
        if self._values and kwh > self._values[-1]:
            # The meter rose: this reading stands for the ones just before
            # it, also for the short steps of a meter read faster than it counts.
            while len(self._times) >= 2 and at - self._times[-2] < _RISING_SPACING_SECONDS:
                del self._times[-1], self._values[-1]
        self._times.append(at)
        self._values.append(kwh)
        if len(self._times) > _MAX_READINGS:
            self._thin()

    def _thin(self) -> None:
        """Make room without losing the oldest reading, where a plug-in starts.

        A reading between two others of a rise goes, as long as its
        neighbours are close enough to interpolate; pass after pass, oldest
        first. Beside a step of a meter that counts coarsely it goes too, but
        a reading that ends or begins a longer rest stays: interpolating over
        it would move energy into a time the meter showed none, such as before
        a plug-in or during a pause. The readings around a known plug-in or
        unplug stay too, however short the rest before it. Only where no such
        reading is left does the oldest other reading go, never the first.
        """
        times, values = self._times, self._values
        target = _MAX_READINGS * 3 // 4
        while len(times) > target:
            kept = self._anchored(times)
            keep = [True] * len(times)
            left, index = len(times), 1
            while index < len(times) - 1 and left > target:
                before, at, after = times[index - 1], times[index], times[index + 1]
                if (
                    index not in kept
                    and values[index - 1] <= values[index] <= values[index + 1]
                    and values[index - 1] < values[index + 1]
                    and after - before <= MAX_INTERPOLATION_SECONDS
                    and (values[index - 1] < values[index] or at - before <= _STEP_SECONDS)
                    and (values[index] < values[index + 1] or after - at <= _STEP_SECONDS)
                ):
                    keep[index] = False
                    left -= 1
                    index += 2
                else:
                    index += 1
            if left == len(times):
                break
            times = [at for at, kept in zip(times, keep, strict=True) if kept]
            values = [kwh for kwh, kept in zip(values, keep, strict=True) if kept]
        self._times, self._values = times, values
        while len(self._times) > _MAX_READINGS:
            kept = self._anchored(self._times)
            index = next((i for i in range(1, len(self._times)) if i not in kept), 1)
            del self._times[index], self._values[index]

    def _anchored(self, times: list[float]) -> set[int]:
        """The readings on both sides of each known plug-in and unplug."""
        kept: set[int] = set()
        for moment in self.anchors:
            index = bisect_right(times, moment)
            kept.update({index - 1, index})
        return kept

    def dump(self) -> list[list[float]]:
        return [[at, kwh] for at, kwh in zip(self._times, self._values, strict=True)]

    def restore(self, raw: object) -> None:
        """Take saved readings; anything out of order is refused as a whole."""
        if not isinstance(raw, list) or len(raw) > _MAX_STORED:
            raise ValueError("invalid meter log")
        times: list[float] = []
        values: list[float] = []
        for item in raw:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("invalid meter reading")
            at, kwh = _moment(item[0]), _moment(item[1])
            if at is None or kwh is None or kwh < 0 or (times and (at <= times[-1] or kwh < values[-1])):
                raise ValueError("invalid meter reading")
            times.append(at)
            values.append(kwh)
        self._times, self._values = times, values
        if len(times) > _MAX_READINGS:
            self._thin()

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


_KEYS = frozenset({
    "known_since", "present", "plugged_at", "charging_at", "note", "last", "last_note",
})


class ChargeMeter:
    """The meter log and the times of the plug-in the service measured from end to end.

    Whether a car is there is the plug cycle's to say; this keeps, on the
    wall clock and across restarts, when that began and ended, so the energy
    in between can be read from the log.

    The service outlives a restart of Home Assistant, so what it measures
    here is whole where Home Assistant alone would have a gap. Home Assistant
    may attach a note to the running cycle, such as the car it recognized; the
    note stays with the cycle and means nothing to the service.
    """

    def __init__(self) -> None:
        self.log = MeterLog()
        self.last: tuple[float, float] | None = None
        self.last_note: str | None = None
        # Since when presence has been known without a long gap.
        self.known_since: float | None = None
        self._gap = PresenceGap()
        self._lose_cycle()

    def _lose_cycle(self) -> None:
        """Nobody saw the whole cycle any more: it cannot be measured."""
        self.present: bool | None = None
        self.plugged_at: float | None = None
        self.charging_at: float | None = None
        self.note: str | None = None

    def observe(
        self, now: float, *, present: bool | None, charging: bool = False,
        reading: float | None = None,
    ) -> None:
        """One poll: whether a car is plugged in, and the meter if it was read."""
        if reading is not None:
            self.log.observe(now, reading)
        try:
            self._observe_presence(now, present, charging)
        finally:
            self._anchor()
            # Only the running and the last cycle are ever measured.
            starts = [at for at in (self.plugged_at, self.last[0] if self.last is not None else None) if at is not None]
            self.log.forget_before(min(starts, default=now) - MAX_INTERPOLATION_SECONDS)

    def _observe_presence(self, now: float, present: bool | None, charging: bool) -> None:
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
                self.last, self.last_note = (self.plugged_at, now), self.note
            self._lose_cycle()
            self.present = False
            return
        if self.present is False:
            self.plugged_at = now
        self.present = True
        if charging and self.charging_at is None and self.plugged_at is not None:
            self.charging_at = now

    def _anchor(self) -> None:
        self.log.anchors = _anchors(self.plugged_at, self.last)

    def annotate(self, plugged_at: float, note: str | None) -> bool:
        """Attach a note to the running cycle; False when that cycle is over."""
        if self.plugged_at is None or self.plugged_at != plugged_at:
            return False
        self.note = _note(note)
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "known_since": self.known_since,
            "present": self.present,
            "plugged_at": self.plugged_at,
            "charging_at": self.charging_at,
            "note": self.note,
            "last": list(self.last) if self.last is not None else None,
            "last_note": self.last_note,
        }

    def dump(self) -> dict[str, Any]:
        """Everything a restarted service needs to go on measuring."""
        return {**self.snapshot(), "known_at": self._gap.known_at, "readings": self.log.dump()}

    def restore(self, raw: dict[str, Any]) -> None:
        """Continue from a saved state; the first poll judges the time since."""
        if raw.keys() != _KEYS | {"known_at", "readings"}:
            raise ValueError("invalid charge meter")
        state = parse_charge_meter({key: raw[key] for key in _KEYS})
        known_at = _moment(raw["known_at"])
        log = MeterLog()
        log.anchors = _anchors(state["plugged_at"], state["last"])
        log.restore(raw["readings"])
        self.log = log
        self.known_since = state["known_since"]
        self.present, self.plugged_at = state["present"], state["plugged_at"]
        self.charging_at, self.note = state["charging_at"], state["note"]
        self.last, self.last_note = state["last"], state["last_note"]
        self._gap.resume(known_at)


def _anchors(plugged_at: float | None, last: tuple[float, float] | None) -> tuple[float, ...]:
    """The plug-ins and unplugs whose energy is asked for."""
    return tuple(at for at in (plugged_at, *(last or ())) if at is not None)


def parse_charge_meter(raw: object) -> dict[str, Any]:
    """Validate what a service reports of its charge meter."""
    if not isinstance(raw, dict) or raw.keys() != _KEYS or (raw["present"] is not None and type(raw["present"]) is not bool):
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
        "known_since": _moment(raw["known_since"]),
        "present": raw["present"],
        "plugged_at": _moment(raw["plugged_at"]),
        "charging_at": _moment(raw["charging_at"]),
        "note": _note(raw["note"]),
        "last": last,
        "last_note": _note(raw["last_note"]),
    }
