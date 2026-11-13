"""Bounded local-calendar proposals for the existing service charging jobs.

A saved weekly plan is a recipe, not charging permission. This module has no
network, filesystem or Modbus side effects. Its absolute proposal still needs
the existing job controller's fresh session, revision and permission checks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .charge_reserve import reserve_energy
from .energy_goal import EnergyGoal

MAX_EXCEPTIONS = 32
MAX_HORIZON_DAYS = 31
MAX_PREVIEW_ROWS = 32
MAX_CHARGE_MINUTES = 1440
_ID = re.compile(r"[0-9a-f]{32}")
_PROFILE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_REQUIRED = frozenset({
    "id", "vehicle_profile", "timezone", "weekdays", "departure_time",
    "charge_minutes", "current_a", "energy_kwh",
})
_OPTIONAL = frozenset({"enabled", "ambiguous_time", "exceptions", "reserve_kwh"})


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("weekly time must be an aware datetime")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("weekly time must be an aware datetime")
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as err:
        raise ValueError("weekly time must be a representable aware datetime") from err


def _local_time(value: object) -> time:
    if not isinstance(value, str) or _TIME.fullmatch(value) is None:
        raise ValueError("weekly departure must use HH:MM")
    return time(int(value[:2]), int(value[3:]))


def _local_date(value: object) -> date:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise ValueError("weekly exception date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise ValueError("weekly exception date is invalid") from err


def _zone(value: object) -> ZoneInfo:
    # Bound the key before asking ZoneInfo to load anything. Do not accept a
    # filesystem path or choose the host's local timezone as a fallback.
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 128
        or any(part in ("", ".", "..") for part in value.split("/"))
        or re.fullmatch(r"[A-Za-z0-9_+./-]+", value) is None
    ):
        raise ValueError("weekly timezone must be an IANA key")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise ValueError("weekly timezone is unavailable") from err


def _integer(value: object, low: int, high: int, field: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"weekly {field} must be an integer from {low} to {high}")
    return value


def _instants(day: date, clock: time, zone: ZoneInfo) -> tuple[datetime, ...]:
    """Round-trip both folds; a nonexistent wall time must not be shifted.

    Compare and deduplicate in UTC, because datetime comparison with identical
    tzinfo objects ignores fold. Normal local times yield one instant; a fold
    yields two; a skipped clock interval/date yields none.
    """
    instants: set[datetime] = set()
    for fold in (0, 1):
        local = datetime.combine(day, clock, zone).replace(fold=fold)
        try:
            instant = local.astimezone(UTC)
            back = instant.astimezone(zone)
        except (OverflowError, ValueError):
            continue
        if back.date() == day and back.time() == clock and back.fold == fold:
            instants.add(instant)
    return tuple(sorted(instants))


@dataclass(frozen=True, slots=True)
class WeeklyException:
    """A local-date override: None skips, otherwise it replaces/adds a trip."""

    day: date
    departure: time | None


@dataclass(frozen=True, slots=True)
class WeeklyOccurrence:
    """An immutable preview row; the occurrence itself never grants release."""

    identifier: str
    vehicle_profile: str
    local_date: date
    local_departure: time
    timezone: str
    start_at: datetime | None
    finish_at: datetime | None
    current_a: float
    energy_kwh: float
    status: str
    reason: str | None
    reserve_kwh: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "vehicle_profile": self.vehicle_profile,
            "date": self.local_date.isoformat(),
            "departure_time": self.local_departure.isoformat(timespec="minutes"),
            "timezone": self.timezone,
            "start_at": self.start_at.isoformat() if self.start_at is not None else None,
            "finish_at": self.finish_at.isoformat() if self.finish_at is not None else None,
            "current_a": self.current_a,
            "energy_kwh": self.energy_kwh,
            **({"reserve_kwh": self.reserve_kwh} if self.reserve_kwh else {}),
            "status": self.status,
            "reason": self.reason,
        }

    def job_request(self, *, now: datetime, vehicle_profile: str) -> dict[str, Any]:
        """Freeze one proposal for the current service job contract.

        Call only at preview time; retain the exact result for confirmation and
        lost-response reconciliation. Repeated previews are not idempotent
        confirmation. A matching profile string is not vehicle authentication.
        """
        current = _aware_utc(now)
        if vehicle_profile != self.vehicle_profile:
            raise ValueError("weekly vehicle profile does not match")
        if self.status != "ready" or self.start_at is None or self.finish_at is None:
            raise ValueError("weekly occurrence has no valid charging window")
        if self.finish_at <= current or self.finish_at - current > timedelta(days=7):
            raise ValueError("weekly job must finish within the next seven days")
        return {
            "id": self.identifier,
            "start_at": max(current, self.start_at).isoformat(),
            "finish_at": self.finish_at.isoformat(),
            "current_a": self.current_a,
            "energy_kwh": self.energy_kwh,
            **({"reserve_kwh": self.reserve_kwh} if self.reserve_kwh else {}),
        }


@dataclass(frozen=True, slots=True)
class WeeklyPlan:
    """Validated recurring recipe; persist snapshots, not Python objects.

    Weekdays use Monday=0 through Sunday=6. charge_minutes is elapsed time,
    not a difference between local clocks. It is a configured charging window,
    not an assurance that the energy ceiling can be reached before departure.
    """

    identifier: str
    vehicle_profile: str
    timezone: str
    weekdays: tuple[int, ...]
    departure: time
    charge_minutes: int
    current_a: float
    energy_kwh: float
    enabled: bool
    ambiguous_time: str
    exceptions: tuple[WeeklyException, ...]
    reserve_kwh: float = 0.0

    @classmethod
    def parse(cls, payload: object) -> WeeklyPlan:
        if (
            not isinstance(payload, dict) or not payload.keys() >= _REQUIRED
            or payload.keys() - (_REQUIRED | _OPTIONAL)
        ):
            raise ValueError("invalid weekly plan fields")
        identifier, profile = payload["id"], payload["vehicle_profile"]
        if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
            raise ValueError("invalid weekly plan id")
        if not isinstance(profile, str) or _PROFILE.fullmatch(profile) is None:
            raise ValueError("invalid weekly vehicle profile")
        zone = _zone(payload["timezone"])
        days = payload["weekdays"]
        if (
            not isinstance(days, list) or not 1 <= len(days) <= 7
            or any(type(day) is not int or not 0 <= day <= 6 for day in days)
            or len(set(days)) != len(days)
        ):
            raise ValueError("weekly weekdays must be unique integers from 0 to 6")
        departure = _local_time(payload["departure_time"])
        duration = _integer(payload["charge_minutes"], 1, MAX_CHARGE_MINUTES, "charge_minutes")
        current = payload["current_a"]
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not 6 <= current <= 32:
            raise ValueError("weekly current must be between 6 and 32 A")
        energy = EnergyGoal()
        energy.set_target(payload["energy_kwh"], 0.0)
        if energy.target_kwh == 0:
            raise ValueError("weekly energy ceiling must be positive")
        reserve = reserve_energy(payload.get("reserve_kwh", 0), energy.target_kwh)
        enabled = payload.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("weekly enabled must be a boolean")
        ambiguous = payload.get("ambiguous_time", "earlier")
        if not isinstance(ambiguous, str) or ambiguous not in ("earlier", "later"):
            raise ValueError("weekly ambiguous_time must be earlier or later")
        raw_exceptions = payload.get("exceptions", [])
        if not isinstance(raw_exceptions, list) or len(raw_exceptions) > MAX_EXCEPTIONS:
            raise ValueError("too many or invalid weekly exceptions")
        exceptions: list[WeeklyException] = []
        seen: set[date] = set()
        for item in raw_exceptions:
            if not isinstance(item, dict) or item.keys() != {"date", "departure_time"}:
                raise ValueError("invalid weekly exception fields")
            day = _local_date(item["date"])
            if day in seen:
                raise ValueError("duplicate weekly exception date")
            seen.add(day)
            clock = None if item["departure_time"] is None else _local_time(item["departure_time"])
            exceptions.append(WeeklyException(day, clock))
        return cls(
            identifier, profile, zone.key, tuple(sorted(days)), departure, duration,
            float(current), energy.target_kwh, enabled, ambiguous,
            tuple(sorted(exceptions, key=lambda item: item.day)), reserve,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "vehicle_profile": self.vehicle_profile,
            "timezone": self.timezone,
            "weekdays": list(self.weekdays),
            "departure_time": self.departure.isoformat(timespec="minutes"),
            "charge_minutes": self.charge_minutes,
            "current_a": self.current_a,
            "energy_kwh": self.energy_kwh,
            **({"reserve_kwh": self.reserve_kwh} if self.reserve_kwh else {}),
            "enabled": self.enabled,
            "ambiguous_time": self.ambiguous_time,
            "exceptions": [
                {"date": item.day.isoformat(), "departure_time": (
                    item.departure.isoformat(timespec="minutes") if item.departure is not None else None
                )}
                for item in self.exceptions
            ],
        }

    def upcoming(
        self, *, now: datetime, horizon_days: int = 14, limit: int = 16,
    ) -> tuple[WeeklyOccurrence, ...]:
        """List today through horizon_days ahead, never move a skipped DST time.

        A disabled plan has no rows. Skip exceptions stay visible, as do future
        nonexistent wall times. Expired real instants are not proposed again.
        The caller must retain a consumed-occurrence ledger before automating
        execution: deterministic IDs alone are not replay protection.
        """
        current = _aware_utc(now)
        horizon = _integer(horizon_days, 0, MAX_HORIZON_DAYS, "horizon_days")
        maximum = _integer(limit, 1, MAX_PREVIEW_ROWS, "limit")
        if not self.enabled:
            return ()
        zone = _zone(self.timezone)
        try:
            local_now = current.astimezone(zone)
        except (OverflowError, ValueError) as err:
            raise ValueError("weekly local time is outside the supported range") from err
        exceptions = {item.day: item.departure for item in self.exceptions}
        rows: list[WeeklyOccurrence] = []
        for offset in range(horizon + 1):
            try:
                day = local_now.date() + timedelta(days=offset)
            except OverflowError:
                break
            if day not in exceptions and day.weekday() not in self.weekdays:
                continue
            clock = exceptions.get(day, self.departure)
            if clock is None:
                rows.append(self._row(day, self.departure, "skipped", "date_skipped"))
            else:
                instants = _instants(day, clock, zone)
                if not instants:
                    rows.append(self._row(day, clock, "invalid_time", "local_time_does_not_exist"))
                else:
                    finish = instants[0 if self.ambiguous_time == "earlier" else -1]
                    if finish <= current:
                        continue
                    reason = f"ambiguous_time_{self.ambiguous_time}" if len(instants) > 1 else None
                    try:
                        start = finish - timedelta(minutes=self.charge_minutes)
                    except OverflowError:
                        rows.append(self._row(day, clock, "invalid_time", "window_out_of_range"))
                    else:
                        rows.append(self._row(day, clock, "ready", reason, start, finish))
            if len(rows) == maximum:
                break
        return tuple(rows)

    def _row(
        self, day: date, clock: time, status: str, reason: str | None,
        start: datetime | None = None, finish: datetime | None = None,
    ) -> WeeklyOccurrence:
        # Stable across edits of a day's clock/current/energy and DST fold choice.
        # Changing a recipe must not manufacture a different same-day identity.
        material = f"weekly-v1|{self.identifier}|{self.vehicle_profile}|{day.isoformat()}"
        identifier = hashlib.sha256(material.encode()).hexdigest()[:32]
        return WeeklyOccurrence(
            identifier, self.vehicle_profile, day, clock, self.timezone,
            start, finish, self.current_a, self.energy_kwh, status, reason, self.reserve_kwh,
        )
