"""Read BavarianData's local public history service without fetching cloud data.

Protocol verified against JustChr/BavarianData commit
275cefb923af4efc998707caede27038a77fa514 (service_read.py and history/models.py).
Only the span and the charge levels of a record are used. ``energy_kwh``
integrates the car's power reports, which lag behind a changed charging
current by up to half an hour, and ``grid_kwh`` is a different measurement.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime

from homeassistant.core import HomeAssistant, SupportsResponse

from custom_components.mennekes_amtron_rtu.vehicle_sources.types import ChargeRecord

from .identity import DOMAIN, bavarian_vehicle_vin

HISTORY_SERVICE = "get_charging_sessions"
HISTORY_LIMIT = 50
HISTORY_TIMEOUT_SECONDS = 10.0
_MAX_EPOCH = 253402300799.0


def _epoch(value: object) -> float:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError("invalid vehicle charging timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("vehicle charging timestamp has no time zone")
    epoch = stamp.timestamp()
    if not math.isfinite(epoch) or not 0 <= epoch <= _MAX_EPOCH:
        raise ValueError("vehicle charging timestamp is out of range")
    return epoch


def _level(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid vehicle charge level")
    level = float(value)
    if not math.isfinite(level) or not 0 <= level <= 100:
        raise ValueError("vehicle charge level is out of range")
    return level


def _query_time(value: float) -> str:
    return datetime.fromtimestamp(max(0.0, min(value, _MAX_EPOCH)), UTC).isoformat()


def _record(
    raw: dict[str, object], start: float, end: float, *, home_zone: str | None,
) -> ChargeRecord:
    if raw.get("late_start") is not False or raw.get("interrupted") is not False:
        raise ValueError("charging-history record is incomplete")
    if raw.get("charge_method") not in (None, "ac"):
        raise ValueError("charging-history method is not supported")
    assumed = raw.get("location_assumed", False)
    if type(assumed) is not bool:
        raise ValueError("invalid charging-history location evidence")
    location = raw.get("location")
    if location is not None and not isinstance(location, dict):
        raise ValueError("invalid charging-history location")
    if home_zone is not None and not assumed and isinstance(location, dict):
        zone = location.get("zone")
        if zone is not None and (
            not isinstance(zone, str) or zone.strip().casefold() != home_zone.strip().casefold()
        ):
            raise ValueError("charging history is located outside the configured home zone")
    first, last = _level(raw.get("soc_start")), _level(raw.get("soc_end"))
    if not first < last:
        raise ValueError("charging-history record charged nothing")
    return ChargeRecord(start, end, first, last)


def _records(
    response: object, *, home_zone: str | None, expected_vin: str,
) -> tuple[ChargeRecord, ...] | None:
    """The complete charges, oldest first; unusable ones are passed over."""
    if not isinstance(response, dict):
        return None
    rows = response.get("sessions")
    # The public service does not prove whether a full page was truncated.
    if not isinstance(rows, list) or len(rows) >= HISTORY_LIMIT:
        return None
    found: list[ChargeRecord] = []
    for raw in rows:
        if not isinstance(raw, dict):
            return None
        vin = raw.get("vin")
        # Another car in the answer means the selection is not what it seems.
        if not isinstance(vin, str) or vin.strip().upper() != expected_vin:
            return None
        try:
            start, end = _epoch(raw.get("start")), _epoch(raw.get("end"))
            if end <= start:
                continue
            found.append(_record(raw, start, end, home_zone=home_zone))
        except (ValueError, OverflowError, OSError):
            # Interrupted, without charge levels, elsewhere: not this one.
            continue
    return tuple(sorted(found, key=lambda record: record.end))


async def async_read_records(
    hass: HomeAssistant, device_id: str, *, since: float, until: float,
    home_zone: str | None = None,
    expected_vin: str | None = None,
) -> tuple[ChargeRecord, ...] | None:
    """Read one bounded local history page; callers handle service/timeout errors.

    Returns the complete AC records of the selected vehicle, oldest first,
    with their own time span and charge levels. A record that is interrupted,
    started late or lacks charge levels is passed over. Missing optional
    location evidence neither proves nor disproves location.
    """
    selected_vin = bavarian_vehicle_vin(hass, device_id)
    if selected_vin is None or (expected_vin is not None and expected_vin != selected_vin):
        return None
    if home_zone is not None and (not isinstance(home_zone, str) or not home_zone.strip()):
        return None
    if not since < until or (
        not hass.services.has_service(DOMAIN, HISTORY_SERVICE)
        or hass.services.supports_response(DOMAIN, HISTORY_SERVICE) is SupportsResponse.NONE
    ):
        return None
    async with asyncio.timeout(HISTORY_TIMEOUT_SECONDS):
        response = await hass.services.async_call(
            DOMAIN, HISTORY_SERVICE,
            {
                "device_id": device_id,
                "vin": selected_vin,
                "from": _query_time(since),
                "to": _query_time(until),
                "limit": HISTORY_LIMIT,
            },
            blocking=True, return_response=True,
        )
    if bavarian_vehicle_vin(hass, device_id) != selected_vin:
        return None
    return _records(response, home_zone=home_zone, expected_vin=selected_vin)
