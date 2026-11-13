"""Guest presets create ordinary bounded orders, never vehicle permissions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

MAX_GUEST_MINUTES = 24 * 60


def guest_request(request: object, *, now: datetime) -> dict[str, Any]:
    """Freeze relative UI inputs into an absolute, expiring confirmation.

    The job controller still validates current, energy, connection scope and
    authorization. No vehicle state, battery level or remembered target is read.
    """
    if not isinstance(request, dict) or request.keys() != {
        "id", "duration_minutes", "current_a", "energy_kwh",
    }:
        raise ValueError("invalid guest request fields")
    duration = request["duration_minutes"]
    if type(duration) is not int or not 1 <= duration <= MAX_GUEST_MINUTES:
        raise ValueError("guest duration must be 1 to 1440 whole minutes")
    return {
        "id": request["id"], "kind": "guest", "start_at": now.isoformat(),
        "finish_at": (now + timedelta(minutes=duration)).isoformat(),
        "current_a": request["current_a"], "energy_kwh": request["energy_kwh"],
    }
