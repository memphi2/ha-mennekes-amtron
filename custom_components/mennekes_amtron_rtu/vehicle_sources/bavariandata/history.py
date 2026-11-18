"""Read BavarianData's local public history service without fetching cloud data.

Protocol verified against JustChr/BavarianData commit
275cefb923af4efc998707caede27038a77fa514 (service_read.py and history/models.py).
``energy_kwh`` estimates battery-side energy from the recorded stream;
``grid_kwh`` is a different measurement and must never substitute for it.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.core import HomeAssistant, SupportsResponse

from custom_components.mennekes_amtron_rtu.vehicle_sources.types import ChargeRecord

from .identity import DOMAIN, bavarian_vehicle_vin

HISTORY_SERVICE = "get_charging_sessions"
HISTORY_LIMIT = 50
HISTORY_TIMEOUT_SECONDS = 10.0
# Below this a span is too short for the wallbox meter's reading interval.
MIN_VEHICLE_KWH = 1.0
_MAX_EPOCH = 253402300799.0


@dataclass(frozen=True, slots=True)
class _Record:
    start: float
    end: float
    energy: float


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


def _energy(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid vehicle charging energy")
    try:
        energy = float(value)
    except OverflowError as err:
        raise ValueError("vehicle charging energy is out of range") from err
    if not math.isfinite(energy) or energy <= 0:
        raise ValueError("vehicle charging energy is out of range")
    return energy


def _query_time(value: float) -> str:
    return datetime.fromtimestamp(max(0.0, min(value, _MAX_EPOCH)), UTC).isoformat()


def _record(
    raw: dict[str, object], start: float, end: float, *, home_zone: str | None,
) -> _Record:
    if raw.get("late_start") is not False or raw.get("interrupted") is not False:
        raise ValueError("charging-history energy is incomplete")
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
    return _Record(start, end, _energy(raw.get("energy_kwh")))


def _latest(
    response: object, *, home_zone: str | None, expected_vin: str,
) -> ChargeRecord | None:
    """The newest record that can be measured; unusable ones are passed over."""
    if not isinstance(response, dict):
        return None
    rows = response.get("sessions")
    # The public service does not prove whether a full page was truncated.
    if not isinstance(rows, list) or not rows or len(rows) >= HISTORY_LIMIT:
        return None
    latest: _Record | None = None
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
            item = _record(raw, start, end, home_zone=home_zone)
        except (ValueError, OverflowError, OSError):
            # Interrupted, without battery energy, elsewhere: not this one.
            continue
        if item.energy >= MIN_VEHICLE_KWH and (latest is None or item.end > latest.end):
            latest = item
    return None if latest is None else ChargeRecord(latest.start, latest.end, latest.energy)


async def async_read_latest(
    hass: HomeAssistant, device_id: str, *, since: float, until: float,
    home_zone: str | None = None,
    expected_vin: str | None = None,
) -> ChargeRecord | None:
    """Read one bounded local history page; callers handle service/timeout errors.

    Returns the newest complete AC record of the selected vehicle with its
    own time span and battery energy. A record that is interrupted, started
    late, lacks battery energy or is too small to measure is passed over.
    Missing optional location evidence neither proves nor disproves location.
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
    return _latest(response, home_zone=home_zone, expected_vin=selected_vin)
