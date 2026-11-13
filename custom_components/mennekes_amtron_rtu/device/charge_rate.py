"""Bounded measured AC rates; no battery assumptions or control authority."""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .charge_job import ChargeJob
    from .charge_job_controller import JobInputs

MAX_BUCKETS = 64
MAX_RATES = 16
LIVE_RATES = 8
MIN_SAMPLES = 3
MIN_INTERVAL = 30.0
MAX_INTERVAL = 180.0
HISTORY_SECONDS = 30 * 86400.0
MAX_EPOCH = 4102444800.0
_HASH = re.compile(r"[0-9a-f]{64}")
type RateKey = tuple[str, int, int]
type LiveKey = tuple[str, str | None, int, int]


def finite(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def context(scope: object, profile: object, amps: object, phases: object) -> LiveKey | None:
    current = finite(amps, 6, 32)
    if (
        not isinstance(scope, str) or _HASH.fullmatch(scope) is None
        or (profile is not None and (not isinstance(profile, str) or _HASH.fullmatch(profile) is None))
        or current is None or type(phases) is not int or phases not in (0, 1, 2, 3)
    ):
        return None
    return scope, profile, round(current * 1000), phases


@dataclass(frozen=True, slots=True)
class RateBucket:
    rates: tuple[float, ...]
    updated_at: float


class RateHistory:
    """Bound rates by hashed source, current and phases, not by raw identity."""

    def __init__(self) -> None:
        self.records: dict[RateKey, RateBucket] = {}
        self.generation = 0

    def add(self, key: LiveKey, rate: float, at: float) -> None:
        _scope, profile, current, phases = key
        if profile is None or phases == 0:
            return
        known = (profile, current, phases)
        previous = self.records.get(known)
        rates = (() if previous is None or not 0 <= at - previous.updated_at <= HISTORY_SECONDS else previous.rates)
        self.records = {k: value for k, value in self.records.items() if 0 <= at - value.updated_at <= HISTORY_SECONDS}
        self.records[known] = RateBucket((*rates, rate)[-MAX_RATES:], at)
        if len(self.records) > MAX_BUCKETS:
            oldest = min(self.records, key=lambda item: self.records[item].updated_at)
            del self.records[oldest]
        self.generation += 1

    def get(self, key: LiveKey, now: float) -> RateBucket | None:
        _scope, profile, current, phases = key
        if profile is None or phases == 0:
            return None
        value = self.records.get((profile, current, phases))
        return value if value is not None and 0 <= now - value.updated_at <= HISTORY_SECONDS else None

    def snapshot(self) -> list[dict[str, Any]]:
        return [{"source": key[0], "current_ma": key[1], "phases": key[2],
                 "rates_kw": list(value.rates), "updated_at": value.updated_at}
                for key, value in sorted(self.records.items())]

    @classmethod
    def restore(cls, raw: object) -> RateHistory:
        if not isinstance(raw, list) or len(raw) > MAX_BUCKETS:
            raise ValueError("invalid rate history size")
        result = cls()
        for item in raw:
            if not isinstance(item, dict) or item.keys() != {"source", "current_ma", "phases", "rates_kw", "updated_at"}:
                raise ValueError("invalid rate history fields")
            source, current, phases = item["source"], item["current_ma"], item["phases"]
            if (
                not isinstance(source, str) or _HASH.fullmatch(source) is None
                or type(current) is not int or not 6000 <= current <= 32000
                or type(phases) is not int or phases not in (1, 2, 3)
            ):
                raise ValueError("invalid rate history context")
            values = item["rates_kw"]
            at = finite(item["updated_at"], 0, MAX_EPOCH)
            if not isinstance(values, list) or not 1 <= len(values) <= MAX_RATES or at is None:
                raise ValueError("invalid rate history observations")
            ceiling = min(30.0, current * phases * 300 / 1_000_000)
            rates = [finite(value, 0.05, ceiling) for value in values]
            if any(value is None for value in rates):
                raise ValueError("invalid rate history value")
            key = (source, current, phases)
            if key in result.records:
                raise ValueError("duplicate rate history context")
            result.records[key] = RateBucket(tuple(value for value in rates if value is not None), at)
        return result


@dataclass(frozen=True, slots=True)
class MeterPoint:
    energy: float
    reported_at: float
    monotonic: float


class ChargeRateLearner:
    """Learn only fresh intervals at unchanged connection/current/phase context."""

    def __init__(self) -> None:
        self.history = RateHistory()
        self.reason = "learning"
        self._key: LiveKey | None = None
        self._anchor: MeterPoint | None = None
        self._previous: MeterPoint | None = None
        self._rates: deque[float] = deque(maxlen=LIVE_RATES)

    def invalidate(self, reason: str = "learning") -> None:
        self._key = None
        self._anchor = self._previous = None
        self._rates.clear()
        self.reason = reason

    def check_context(self, key: LiveKey | None, *, charging: bool) -> None:
        if key is None or not charging:
            self.invalidate("not_charging")
        elif key != self._key:
            self.invalidate()
            self._key = key

    def observe(self, key: LiveKey | None, *, energy: object, at: object, monotonic: float, now: float, charging: bool) -> None:
        self.check_context(key, charging=charging)
        if key is None or not charging:
            return
        total, stamp = finite(energy, 0, 1e12), finite(at, 0, MAX_EPOCH)
        tick, wall = finite(monotonic, 0, 1e12), finite(now, 0, MAX_EPOCH)
        if total is None or stamp is None or tick is None or wall is None or not 0 <= wall - stamp <= MAX_INTERVAL:
            self.invalidate("meter_unavailable")
            return
        point = MeterPoint(total, stamp, tick)
        previous = self._previous
        if previous is not None:
            if point.reported_at == previous.reported_at and point.energy == previous.energy:
                return  # A cached observation does not renew sample freshness.
            if point.energy < previous.energy or point.reported_at <= previous.reported_at or point.monotonic <= previous.monotonic:
                self.invalidate("meter_reset")
                return
        anchor = self._anchor
        self._previous = point
        if anchor is None:
            self._anchor = point
            return
        seconds = point.monotonic - anchor.monotonic
        wall_seconds = point.reported_at - anchor.reported_at
        if seconds > MAX_INTERVAL or abs(wall_seconds - seconds) > 5:
            self.invalidate("measurement_gap")
            return
        if seconds < MIN_INTERVAL:
            return
        rate = (point.energy - anchor.energy) * 3600 / seconds
        self._anchor = point
        ceiling = min(30.0, key[2] * (key[3] or 3) * 300 / 1_000_000)
        if not 0.05 <= rate <= ceiling:
            self._rates.clear()
            self.reason = "no_energy_progress" if rate < 0.05 else "meter_jump"
            return
        self._rates.append(rate)
        self.history.add(key, rate, stamp)
        self.reason = "ready" if len(self._rates) >= MIN_SAMPLES else "learning"

    def rates(self, key: LiveKey, *, now: float, monotonic: float, historical: bool) -> tuple[tuple[float, ...], str, float | None, float | None]:
        previous = self._previous
        if (
            key == self._key and previous is not None and len(self._rates) >= MIN_SAMPLES
            and 0 <= monotonic - previous.monotonic <= MAX_INTERVAL
            and 0 <= now - previous.reported_at <= MAX_INTERVAL
        ):
            return tuple(self._rates), "live", previous.reported_at, previous.energy
        known = self.history.get(key, now) if historical else None
        if known is not None and len(known.rates) >= MIN_SAMPLES:
            return known.rates, "history", known.updated_at, None
        return (), "none", None, None


def _finish(job: ChargeJob, remaining: float, delivered: float, now: datetime, rate: float) -> datetime:
    """Include the accepted reserve/boost and the original waiting window."""
    if now >= job.start_at:
        return now + timedelta(hours=remaining / rate)
    early = max(0.0, job.reserve.target_kwh - delivered) if job.reserve is not None and not job.reserve.reached else 0.0
    if job.boost_until is not None and job.boost_until > now:
        early = max(early, rate * (job.boost_until - now).total_seconds() / 3600)
    early = min(remaining, early)
    early_end = now + timedelta(hours=early / rate)
    if early >= remaining:
        return early_end
    return max(job.start_at, early_end) + timedelta(hours=(remaining - early) / rate)


def charging_forecast(
    learner: ChargeRateLearner, job: ChargeJob | None, sample: JobInputs, *,
    profile: str | None, phases: int, monotonic: float, charging: bool, meter_at: object = None,
) -> dict[str, Any]:
    """Describe a conditional AC-ceiling estimate, never new permission."""
    result: dict[str, Any] = {"basis": "ac_meter", "state": "no_job", "source": "none",
                              "power_kw": None, "remaining_kwh": None, "earliest_finish": None,
                              "latest_finish": None, "deadline_status": "unknown", "samples": 0,
                              "observed_at": None}
    if job is None:
        return result
    if job.terminal:
        result["state"] = "complete" if job.state == "reached" else "stopped"
        return result
    baseline, total = job.energy.baseline_kwh, finite(sample.total_kwh, 0, 1e12)
    result["state"] = "unavailable"
    stamp = finite(meter_at, 0, MAX_EPOCH)
    # A suspended order waits for proof of the same plug-in; nothing to estimate.
    if job.state == "suspended" or not sample.available or sample.present is not True or sample.scope != job.binding or not sample.meter_fresh or baseline is None or total is None or total < baseline or stamp is None or not 0 <= sample.now.timestamp() - stamp <= MAX_INTERVAL:
        return result
    delivered = total - baseline
    remaining = max(0.0, job.energy.target_kwh - delivered)
    result["remaining_kwh"] = round(remaining, 3)
    if remaining == 0:
        result["state"] = "complete"
        return result
    planned_pause = job.state == "scheduled" and sample.now < job.start_at
    amps = min(job.current_a, sample.current_cap) if planned_pause else sample.current_a
    key = context(sample.scope, profile, amps, phases)
    result["state"] = "waiting_vehicle" if not charging and not planned_pause else "learning"
    if key is None or (not charging and not planned_pause) or sample.independent_stop or not sample.permitted or sample.pending:
        return result
    now = sample.now.timestamp()
    rates, source, observed, live_energy = learner.rates(key, now=now, monotonic=monotonic, historical=planned_pause)
    if not rates:
        result["state"] = "scheduled" if planned_pause else "stale" if learner.reason == "ready" else learner.reason
        return result
    if source == "live" and (observed != stamp or live_energy is None or not math.isclose(live_energy, total, rel_tol=0, abs_tol=1e-6)):
        result["state"] = "awaiting_meter"
        return result
    typical = median(rates)
    slow, fast = min(rates) * 0.85, max(rates) * 1.15
    result.update(source=source, power_kw=round(typical, 3), samples=len(rates),
                  observed_at=datetime.fromtimestamp(observed, UTC).isoformat() if observed is not None else None)
    if slow < 0.05 or fast / slow > 3:
        result["state"] = "unstable"
        return result
    if remaining / slow > 7 * 24:
        result.update(state="beyond_horizon", deadline_status="at_risk")
        return result
    anchor = datetime.fromtimestamp(observed, UTC) if source == "live" and observed is not None else sample.now
    earliest = _finish(job, remaining, delivered, anchor, fast)
    latest = _finish(job, remaining, delivered, anchor, slow)
    if latest <= sample.now:
        result["state"] = "awaiting_meter"
        return result
    earliest = max(earliest, sample.now)
    deadline = "likely" if latest <= job.finish_at else "at_risk" if earliest <= job.finish_at else "unlikely"
    result.update(state="estimate", earliest_finish=earliest.isoformat(), latest_finish=latest.isoformat(), deadline_status=deadline)
    return result
