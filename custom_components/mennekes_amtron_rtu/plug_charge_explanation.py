"""Read-only vehicle evidence explanations, without identities or live counters."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .vehicle_sources import PLUG_WINDOW

if TYPE_CHECKING:
    from .vehicle_sources import VehicleDetection


def explain_detection(
    readings: list[tuple[bool, VehicleDetection]], since: datetime | None,
) -> dict[str, Any]:
    """Explain the same tri-state evidence the grant uses; never grant a car.

    Unknown and stale signals deliberately share a label: providers already
    collapse both to None, so an explanation cannot invent which one occurred.
    Slots are local to this snapshot, not vehicle identifiers.
    """
    checks: list[dict[str, Any]] = []
    for index, (selected, evidence) in enumerate(readings, 1):
        match = evidence.at_home(since)
        if match is True:
            reason = "matched"
        elif evidence.connected is False:
            reason = "vehicle_unplugged"
        elif evidence.home is False:
            reason = "vehicle_away"
        elif evidence.connected is not True:
            reason = "cable_unknown_or_stale"
        else:
            reason = "location_unknown_or_stale"
        stamp = evidence.connected_at
        report_current = (
            None if since is None or stamp is None or stamp.tzinfo is None
            else stamp >= since - PLUG_WINDOW
        )
        checks.append({
            "slot": index, "selected": selected, "connected": evidence.connected,
            "home": evidence.home, "parked_home": evidence.parked_home,
            "matches": match, "report_current": report_current, "reason": reason,
        })
    matches = sum(check["matches"] is True for check in checks)
    unknown = sum(check["matches"] is None for check in checks)
    if not checks:
        reason = "detection_not_configured"
    elif matches > 1:
        reason = "multiple_vehicles_match"
    elif unknown:
        reason = "vehicle_evidence_unknown"
    elif not matches:
        reason = "no_vehicle_matches"
    else:
        reason = "vehicle_detected"
    return {"reason": reason, "matching_profiles": matches, "unknown_profiles": unknown, "checks": checks}
