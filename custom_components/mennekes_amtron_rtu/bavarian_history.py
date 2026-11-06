"""Read BavarianData's local public history service without fetching cloud data.

Protocol verified against JustChr/BavarianData commit
275cefb923af4efc998707caede27038a77fa514 (service_read.py and history/models.py).
``energy_kwh`` estimates battery-side energy from the recorded stream;
``grid_kwh`` is a different measurement and must never substitute for it.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import device_registry as dr

from .device.charging_session import CompletedSession

HISTORY_DOMAIN = "bavariandata"
HISTORY_SERVICE = "get_charging_sessions"
HISTORY_LIMIT = 50
HISTORY_TIMEOUT_SECONDS = 10.0
MATCH_TOLERANCE_SECONDS = 60.0
_MAX_EPOCH = 253402300799.0


def bavarian_vehicle_vin(hass: HomeAssistant, device_id: object) -> str | None:
    """Resolve a real vehicle, never BavarianData's account-wide debug device.

    BavarianData identifies vehicle devices as (domain, VIN), while its debug
    device uses the config-entry ID. Its history service permits account-wide
    queries, so the integration must establish a single vehicle before calling.
    """
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 256:
        return None
    try:
        device = dr.async_get(hass).async_get(device_id)
    except RuntimeError:  # The registry is not ready yet.
        return None
    if device is None:
        return None
    candidates = {
        value.strip().upper() for domain, value in device.identifiers
        if domain == HISTORY_DOMAIN and value != device.config_entry_id
        and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", value.strip().upper())
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


@dataclass(frozen=True, slots=True)
class HistoryMatch:
    """A complete matching history interval, with estimated battery energy."""

    vehicle_kwh: float
    records: int


@dataclass(frozen=True, slots=True)
class _Record:
    vin: str
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
    vin = raw.get("vin")
    if not isinstance(vin, str) or not vin.strip() or len(vin) > 64:
        raise ValueError("invalid charging-history vehicle identity")
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
    return _Record(vin.strip().upper(), start, end, _energy(raw.get("energy_kwh")))


def _match_history(
    response: object, session: CompletedSession, *, home_zone: str | None,
    expected_vin: str,
) -> HistoryMatch | None:
    if not isinstance(response, dict):
        return None
    rows = response.get("sessions")
    # The public service does not prove whether a full page was truncated.
    if not isinstance(rows, list) or not rows or len(rows) >= HISTORY_LIMIT:
        return None
    lower = session.connected_at - MATCH_TOLERANCE_SECONDS
    upper = session.disconnected_at + MATCH_TOLERANCE_SECONDS
    records: dict[tuple[str, float], _Record] = {}
    try:
        for raw in rows:
            if not isinstance(raw, dict):
                return None
            vin = raw.get("vin")
            if not isinstance(vin, str) or vin.strip().upper() != expected_vin:
                return None
            start = _epoch(raw.get("start"))
            if start > upper:
                continue
            end = _epoch(raw.get("end"))
            if end < start:
                return None
            if end < lower:
                continue
            if start < lower or end > upper:
                return None
            item = _record(raw, start, end, home_zone=home_zone)
            key = (item.vin, item.start)
            if key in records and records[key] != item:
                return None
            records[key] = item
    except (ValueError, OverflowError, OSError):
        return None
    ordered = sorted(records.values(), key=lambda row: row.start)
    if not ordered or len({item.vin for item in ordered}) != 1:
        return None
    if any(previous.end > following.start for previous, following in pairwise(ordered)):
        return None
    if not (
        session.started_after - MATCH_TOLERANCE_SECONDS
        <= ordered[0].start <= session.started_before + MATCH_TOLERANCE_SECONDS
        and session.ended_after - MATCH_TOLERANCE_SECONDS
        <= ordered[-1].end <= session.ended_before + MATCH_TOLERANCE_SECONDS
    ):
        return None
    try:
        total = math.fsum(item.energy for item in ordered)
    except OverflowError:
        return None
    return HistoryMatch(total, len(ordered))


async def async_read_history(
    hass: HomeAssistant, device_id: str, session: CompletedSession, *,
    home_zone: str | None = None,
    expected_vin: str | None = None,
) -> HistoryMatch | None:
    """Read one bounded local history page; callers handle service/timeout errors.

    Source timestamps, completeness flags and both charging boundaries must
    agree. Multiple AC records may cover pauses in one plug cycle, but every
    overlapping record must be complete, non-overlapping and for the same VIN.
    Missing optional location evidence neither proves nor disproves location.
    """
    selected_vin = bavarian_vehicle_vin(hass, device_id)
    if selected_vin is None or (expected_vin is not None and expected_vin != selected_vin):
        return None
    if home_zone is not None and (not isinstance(home_zone, str) or not home_zone.strip()):
        return None
    session = CompletedSession.parse(session.as_dict())
    if (
        not hass.services.has_service(HISTORY_DOMAIN, HISTORY_SERVICE)
        or hass.services.supports_response(HISTORY_DOMAIN, HISTORY_SERVICE) is SupportsResponse.NONE
    ):
        return None
    async with asyncio.timeout(HISTORY_TIMEOUT_SECONDS):
        response = await hass.services.async_call(
            HISTORY_DOMAIN, HISTORY_SERVICE,
            {
                "device_id": device_id,
                "vin": selected_vin,
                "from": _query_time(session.connected_at - MATCH_TOLERANCE_SECONDS),
                "to": _query_time(session.disconnected_at + MATCH_TOLERANCE_SECONDS),
                "limit": HISTORY_LIMIT,
            },
            blocking=True, return_response=True,
        )
    if bavarian_vehicle_vin(hass, device_id) != selected_vin:
        return None
    return _match_history(response, session, home_zone=home_zone, expected_vin=selected_vin)
