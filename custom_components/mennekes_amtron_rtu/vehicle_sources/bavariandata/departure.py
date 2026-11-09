"""Read one active BavarianData climate timer from already published HA states.

BavarianData's catalogue exposes action/hour/minute for overwriteTimer and two
weekday timers. It does not expose their weekdays or the vehicle time zone;
those must be supplied explicitly, without guessing a recurring schedule.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .identity import DOMAIN, bavarian_vehicle_vin

_PREFIX = "vehicle.cabin.climate.timers"
_TIMERS = ("overwriteTimer", "weekdaysTimer1", "weekdaysTimer2")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def departure_entities(hass: HomeAssistant, device_id: str) -> list[str]:
    """The enabled timer entities, for display; BMW does not send them for every car."""
    vin = bavarian_vehicle_vin(hass, device_id)
    if vin is None:
        return []
    registry = er.async_get(hass)
    found = []
    for timer in _TIMERS:
        entities = []
        for part in ("action", "hour", "minute"):
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{vin}_{_PREFIX}.{timer}.{part}")
            entry = registry.async_get(entity_id) if entity_id is not None else None
            if entry is None or entry.device_id != device_id or entry.disabled_by is not None:
                break
            entities.append(entity_id)
        else:
            found.append(entities[0])
    return found


def next_timer_occurrence(
    now: datetime, zone: ZoneInfo, hour: int, minute: int,
    weekdays: Collection[str] | None = None,
) -> datetime | None:
    """Resolve a real future instant, skipping DST gaps and considering both folds.

    No weekdays means the next-only timer, bounded to 24 elapsed hours. An
    explicit empty collection disables a recurring timer instead of inventing
    Monday through Friday or all seven days.
    """
    if (
        now.tzinfo is None or now.utcoffset() is None
        or type(hour) is not int or not 0 <= hour <= 23
        or type(minute) is not int or not 0 <= minute <= 59
        or (weekdays is not None and (
            isinstance(weekdays, str) or not weekdays
            or any(day not in _WEEKDAYS for day in weekdays)
        ))
    ):
        return None
    current = now.astimezone(UTC)
    local = current.astimezone(zone)
    candidates: set[datetime] = set()
    for offset in range(2 if weekdays is None else 8):
        date = local.date() + timedelta(days=offset)
        if weekdays is not None and _WEEKDAYS[date.weekday()] not in weekdays:
            continue
        wall_time = datetime.combine(date, time(hour, minute))
        for fold in (0, 1):
            instant = wall_time.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None) != wall_time:
                continue  # This local clock time does not exist on that date.
            if instant <= current or (weekdays is None and instant - current > timedelta(days=1)):
                continue
            candidates.add(instant)
    return min(candidates) if candidates else None


def _clock_part(value: str | None, maximum: int) -> int | None:
    if value is None or re.fullmatch(r"[0-9]{1,2}(?:\.0+)?", value) is None:
        return None
    number = int(value.partition(".")[0])
    return number if number <= maximum else None


def read_bavarian_departure(
    hass: HomeAssistant, device_id: object, *, now: datetime, time_zone: str,
    timer1_days: Collection[str] = (), timer2_days: Collection[str] = (),
) -> datetime | None:
    """Select the next active timer for exactly one registered vehicle, locally."""
    vin = bavarian_vehicle_vin(hass, device_id)
    if vin is None:
        return None
    try:
        zone = ZoneInfo(time_zone)
        registry = er.async_get(hass)
    except (RuntimeError, ValueError, ZoneInfoNotFoundError):
        return None

    def value(descriptor: str) -> str | None:
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{vin}_{descriptor}")
        if entity_id is None:
            return None
        entry = registry.async_get(entity_id)
        if entry is None or entry.device_id != device_id:
            return None
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable") or (
            state.attributes.get("restored") is True
            or state.attributes.get("descriptor") != descriptor
        ):
            return None
        return state.state

    candidates: list[datetime] = []
    for timer, weekdays in zip(_TIMERS, (None, timer1_days, timer2_days), strict=True):
        prefix = f"{_PREFIX}.{timer}"
        if value(f"{prefix}.action") != "activate":
            continue
        hour = _clock_part(value(f"{prefix}.hour"), 23)
        minute = _clock_part(value(f"{prefix}.minute"), 59)
        if hour is None or minute is None:
            continue
        occurrence = next_timer_occurrence(now, zone, hour, minute, weekdays)
        if occurrence is not None:
            candidates.append(occurrence)
    return min(candidates) if candidates else None
