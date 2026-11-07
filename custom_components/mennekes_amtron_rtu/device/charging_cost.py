"""Bounded estimates of charging costs from prices and observed meter increments.

All prices use EUR/kWh on the same gross basis. A changing price is applied from
its observation forward, never retroactively. Energy within a meter interval is
distributed uniformly in time: this is an estimate, not a billing measurement.
Running state is intentionally not persisted; only completed results are saved.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_PRICE_CHANGES = 256
MAX_TARIFF_INTERVAL = 7 * 86400
_MAX_TIMESTAMP = 253402214400.0  # Leave a day for local-zone conversions.
_CONFIG_KEYS = {
    "module", "regular_network", "price_includes_network", "high_network",
    "low_network", "windows", "active_quarters", "tariff_year", "timezone",
}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a finite number")
    try:
        result = float(value)
    except OverflowError as err:
        raise ValueError("value must be a finite number") from err
    if not math.isfinite(result):
        raise ValueError("value must be a finite number")
    return result


def _timestamp(value: object) -> float:
    result = _number(value)
    if not 0 <= result < _MAX_TIMESTAMP:
        raise ValueError("timestamp out of range")
    return result


def _price(value: object) -> float | None:
    try:
        return _number(value)
    except ValueError:
        return None


def _minute(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("window time must be HH:MM")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True, slots=True)
class TariffWindow:
    """A daily half-open local-time window, including windows across midnight."""

    start: int
    end: int
    rate: str

    def __post_init__(self) -> None:
        if any(type(value) is not int or not 0 <= value < 1440 for value in (self.start, self.end)):
            raise ValueError("window minutes out of range")
        if self.start == self.end or self.rate not in ("ht", "nt"):
            raise ValueError("window requires a duration and ht/nt rate")

    def contains(self, minute: int) -> bool:
        if self.start < self.end:
            return self.start <= minute < self.end
        return minute >= self.start or minute < self.end

    def as_dict(self) -> dict[str, str]:
        return {
            "start": f"{self.start // 60:02}:{self.start % 60:02}",
            "end": f"{self.end // 60:02}:{self.end % 60:02}",
            "rate": self.rate,
        }

    @classmethod
    def parse(cls, raw: object) -> TariffWindow:
        if not isinstance(raw, dict) or raw.keys() != {"start", "end", "rate"}:
            raise ValueError("invalid tariff window")
        return cls(_minute(raw["start"]), _minute(raw["end"]), raw["rate"])


@dataclass(frozen=True, slots=True)
class PriceInterval:
    """One price applying to [start, end) in UTC epoch seconds."""

    start: float
    end: float
    price: float | None


@dataclass(frozen=True, slots=True)
class TariffConfig:
    """Network-price replacement, retaining saved whole-minute schedules.

    New user submissions and imports require quarter-hour boundaries at their
    input boundary. Existing valid estimates must not prevent wallbox setup.
    """

    module: str = "none"
    regular_network: float | None = None
    price_includes_network: bool = True
    high_network: float | None = None
    low_network: float | None = None
    windows: tuple[TariffWindow, ...] = ()
    active_quarters: tuple[int, ...] = ()
    tariff_year: int | None = None
    timezone: str = "Europe/Berlin"
    _zone: ZoneInfo = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.module not in ("none", "module_2", "module_3"):
            raise ValueError("invalid module")
        if type(self.price_includes_network) is not bool:
            raise ValueError("price_includes_network must be boolean")
        if not isinstance(self.timezone, str) or len(self.timezone) > 255:
            raise ValueError("invalid timezone")
        try:
            object.__setattr__(self, "_zone", ZoneInfo(self.timezone))
        except (ValueError, ZoneInfoNotFoundError) as err:
            raise ValueError("invalid timezone") from err
        for name in ("regular_network", "high_network", "low_network"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _number(value))
        if self.module == "none":
            return
        if _number(self.regular_network) <= 0:
            raise ValueError("regular_network must be positive")
        if self.module == "module_2":
            return
        if self.tariff_year is None or type(self.tariff_year) is not int or not 2024 <= self.tariff_year <= 9998:
            raise ValueError("invalid tariff_year")
        if not isinstance(self.active_quarters, tuple) or not 2 <= len(self.active_quarters) <= 4 or any(
            type(quarter) is not int or not 1 <= quarter <= 4 for quarter in self.active_quarters
        ) or len(set(self.active_quarters)) != len(self.active_quarters):
            raise ValueError("active_quarters requires two to four distinct quarters")
        regular = _number(self.regular_network)
        if _number(self.high_network) <= regular or not 0 <= _number(self.low_network) < regular:
            raise ValueError("network prices require low_network < regular_network < high_network")
        if not isinstance(self.windows, tuple) or not 2 <= len(self.windows) <= 16 or any(
            not isinstance(window, TariffWindow) for window in self.windows
        ):
            raise ValueError("windows requires two to sixteen tariff windows")
        if {window.rate for window in self.windows} != {"ht", "nt"}:
            raise ValueError("windows requires both ht and nt")
        occupied: set[int] = set()
        for window in self.windows:
            minutes = {minute for minute in range(1440) if window.contains(minute)}
            if occupied & minutes:
                raise ValueError("tariff windows overlap")
            occupied.update(minutes)
        if len(occupied) == 1440:
            raise ValueError("windows must leave a standard tariff interval")

    @classmethod
    def parse(cls, raw: object) -> TariffConfig:
        if not isinstance(raw, dict) or not raw.keys() <= _CONFIG_KEYS:
            raise ValueError("invalid tariff configuration")
        module = raw.get("module", "none")
        if module not in ("none", "module_2", "module_3"):
            raise ValueError("invalid module")
        if module != "none" and "price_includes_network" not in raw:
            raise ValueError("price_includes_network is required")
        windows = raw.get("windows", [])
        quarters = raw.get("active_quarters", [])
        if not isinstance(windows, list) or len(windows) > 16 or not isinstance(quarters, list) or len(quarters) > 4:
            raise ValueError("invalid windows or active_quarters")
        return cls(
            module=module,
            regular_network=raw.get("regular_network"),
            price_includes_network=raw.get("price_includes_network", True),
            high_network=raw.get("high_network"),
            low_network=raw.get("low_network"),
            windows=tuple(TariffWindow.parse(window) for window in windows),
            active_quarters=tuple(quarters),
            tariff_year=raw.get("tariff_year"),
            timezone=raw.get("timezone", "Europe/Berlin"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "module": self.module, "regular_network": self.regular_network,
            "price_includes_network": self.price_includes_network,
            "high_network": self.high_network, "low_network": self.low_network,
            "windows": [window.as_dict() for window in self.windows],
            "active_quarters": list(self.active_quarters),
            "tariff_year": self.tariff_year, "timezone": self.timezone,
        }

    def price_at(self, at: float, base_price: float | None) -> float | None:
        """Return the effective gross price, or unknown for missing/stale input."""
        base = _price(base_price)
        try:
            at = _timestamp(at)
        except ValueError:
            return None
        if base is None or self.module == "none":
            return base
        regular = _number(self.regular_network)
        network = regular * 0.4
        if self.module == "module_3":
            local = datetime.fromtimestamp(at, self._zone)
            if local.year != self.tariff_year:
                return None
            network = regular
            if (local.month - 1) // 3 + 1 in self.active_quarters:
                for window in self.windows:
                    if window.contains(local.hour * 60 + local.minute):
                        network = _number(self.high_network if window.rate == "ht" else self.low_network)
                        break
        return _price(base - regular + network if self.price_includes_network else base + network)

    def price_intervals(
        self, start: float, end: float, base_price: float | None,
    ) -> tuple[PriceInterval, ...]:
        """Split a bounded meter interval at actual UTC instants of price changes.

        Windows have minute precision. Walking UTC minutes also handles skipped
        and repeated local times, quarter boundaries and tariff expiry correctly.
        At most seven days and 256 resulting transitions are accepted per call.
        """
        start, end = _timestamp(start), _timestamp(end)
        if end <= start:
            raise ValueError("price interval must have positive duration")
        if self.module != "module_3" or _price(base_price) is None:
            return (PriceInterval(start, end, self.price_at(start, base_price)),)
        if end - start > MAX_TARIFF_INTERVAL:
            return (PriceInterval(start, end, None),)
        intervals: list[PriceInterval] = []
        segment_start, price = start, self.price_at(start, base_price)
        cursor = (math.floor(start / 60) + 1) * 60
        while cursor < end:
            current = self.price_at(cursor, base_price)
            if current != price:
                intervals.append(PriceInterval(segment_start, cursor, price))
                if len(intervals) >= MAX_PRICE_CHANGES:
                    return (PriceInterval(start, end, None),)
                segment_start, price = cursor, current
            cursor += 60
        intervals.append(PriceInterval(segment_start, end, price))
        return tuple(intervals)

    def average_price(self, start: float, end: float, base_price: float | None) -> float | None:
        intervals = self.price_intervals(start, end, base_price)
        if any(interval.price is None for interval in intervals):
            return None
        return math.fsum(
            _number(interval.price) * ((interval.end - interval.start) / (end - start))
            for interval in intervals
        )


@dataclass(frozen=True, slots=True)
class CostResult:
    """A bounded completed estimate; incomplete never masquerades as zero cost."""

    known_cost: float
    unpriced_kwh: float
    energy_kwh: float
    complete: bool

    def __post_init__(self) -> None:
        known, missing, energy = (_number(value) for value in (self.known_cost, self.unpriced_kwh, self.energy_kwh))
        if type(self.complete) is not bool or not 0 <= missing <= energy:
            raise ValueError("invalid cost result")
        if (self.complete and missing != 0) or (energy == 0 and known != 0):
            raise ValueError("inconsistent cost result")
        object.__setattr__(self, "known_cost", known)
        object.__setattr__(self, "unpriced_kwh", missing)
        object.__setattr__(self, "energy_kwh", energy)

    @property
    def cost(self) -> float | None:
        return self.known_cost if self.complete else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "known_cost": self.known_cost, "unpriced_kwh": self.unpriced_kwh,
            "energy_kwh": self.energy_kwh, "complete": self.complete,
        }

    @classmethod
    def parse(cls, raw: object) -> CostResult:
        if not isinstance(raw, dict) or raw.keys() != {"known_cost", "unpriced_kwh", "energy_kwh", "complete"}:
            raise ValueError("invalid cost result")
        return cls(raw["known_cost"], raw["unpriced_kwh"], raw["energy_kwh"], raw["complete"])


class CostAccumulator:
    """Keep running sums and at most 256 price changes between fresh meter reads.

    Call set_price when an input price changes, including becoming unavailable.
    Only fresh meter reads belong in observe_energy; None means a failed read,
    not an unchanged cached value. Events must arrive in chronological order.
    Call reset for a new fully observed plug cycle, then supply its idle baseline
    and current price. A reset does not carry the previous cycle's price forward.
    """

    def __init__(self, tariff: TariffConfig | None = None, *, max_gap: float = 300.0) -> None:
        self.tariff = tariff or TariffConfig()
        self.max_gap = _number(max_gap)
        if self.max_gap <= 0 or self.max_gap > MAX_TARIFF_INTERVAL:
            raise ValueError("max_gap out of range")
        self.reset()

    def reset(self) -> None:
        self._last_at: float | None = None
        self._meter_at: float | None = None
        self._meter_kwh: float | None = None
        self._price: float | None = None
        self._meter_price: float | None = None
        self._pending: list[tuple[float, float | None]] = []
        self._invalid_interval = False
        self._complete = True
        self._known_cost = 0.0
        self._unpriced_kwh = 0.0
        self._energy_kwh = 0.0

    @property
    def result(self) -> CostResult:
        return CostResult(
            self._known_cost, self._unpriced_kwh, self._energy_kwh,
            self._complete and self._meter_at is not None,
        )

    def _touch(self, at: float) -> float | None:
        try:
            at = _timestamp(at)
        except ValueError:
            self._complete = False
            self._invalid_interval = True
            return None
        if self._last_at is not None and at < self._last_at:
            self._complete = False
            self._invalid_interval = True
            return None
        self._last_at = at
        return at

    def set_price(self, at: float, price: float | None) -> None:
        timestamp = self._touch(at)
        if timestamp is None:
            return
        price = _price(price)
        if price == self._price:
            return
        self._price = price
        if self._meter_at is None or self._invalid_interval:
            return
        if timestamp == self._meter_at:
            self._meter_price = price
        elif self._pending and self._pending[-1][0] == timestamp:
            self._pending[-1] = (timestamp, price)
        elif len(self._pending) < MAX_PRICE_CHANGES:
            self._pending.append((timestamp, price))
        else:
            self._complete = False
            self._invalid_interval = True
            self._pending.clear()

    def observe_energy(self, at: float, lifetime_kwh: float | None) -> None:
        timestamp = self._touch(at)
        if timestamp is None:
            return
        value = _price(lifetime_kwh)
        if value is None or value < 0:
            self._complete = False
            self._invalid_interval = True
            return
        if self._meter_kwh is not None and self._meter_at is not None:
            delta = value - self._meter_kwh
            elapsed = timestamp - self._meter_at
            if delta < 0:
                self._complete = False
            elif elapsed > self.max_gap or self._invalid_interval or (elapsed == 0 and delta > 0):
                self._complete = False
                self._add(delta, 0.0, delta)
            elif delta > 0:
                self._integrate(timestamp, delta)
        self._meter_at, self._meter_kwh = timestamp, value
        self._meter_price = self._price
        self._pending.clear()
        self._invalid_interval = False

    def sample(self, at: float, lifetime_kwh: float | None, unit_price: float | None) -> None:
        """Observe the price applying from now, then a fresh meter reading."""
        self.set_price(at, unit_price)
        self.observe_energy(at, lifetime_kwh)

    def _integrate(self, at: float, delta: float) -> None:
        start = _number(self._meter_at)
        duration, cursor, price = at - start, start, self._meter_price
        weighted_prices: list[float] = []
        unknown_duration = 0.0
        for boundary, next_price in [*self._pending, (at, self._price)]:
            if boundary > cursor:
                for interval in self.tariff.price_intervals(cursor, boundary, price):
                    elapsed = interval.end - interval.start
                    if interval.price is None:
                        unknown_duration += elapsed
                    else:
                        weighted_prices.append(interval.price * (elapsed / duration))
            cursor, price = boundary, next_price
        missing = delta * min(1.0, unknown_duration / duration)
        cost = delta * math.fsum(weighted_prices)
        if unknown_duration > 0:
            self._complete = False
        self._add(delta, cost, missing)

    def _add(self, energy: float, cost: float, missing: float) -> None:
        if not math.isfinite(self._energy_kwh + energy):
            self._complete = False
            return
        if not math.isfinite(self._known_cost + cost):
            cost, missing = 0.0, energy
        self._energy_kwh += energy
        self._known_cost += cost
        self._unpriced_kwh = min(self._energy_kwh, self._unpriced_kwh + missing)
        if missing > 0:
            self._complete = False
