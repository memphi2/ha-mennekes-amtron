"""One bounded durable guest draft, without any permission to charge."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .decode import is_hex32
from .persistence import replace_file

DRAFT_STATES = (
    "ready",
    "job_exists",
    "job_not_permitted",
    "job_current_exceeds_cap",
    "job_preview_expired",
    "job_storage_unavailable",
    "invalid_job_request",
    "job_stop_unconfirmed",
    "guest_vehicle_target_active",
)

FIELDS = frozenset({"duration_minutes", "current_a", "energy_kwh"})


def defaults() -> dict[str, Any]:
    return {"current_a": 6.0, "energy_kwh": 10.0, "duration_minutes": 120}


def validate(values: object) -> dict[str, Any]:
    """Persist bounded inputs, never charging authority."""
    if not isinstance(values, dict) or values.keys() != FIELDS:
        raise ValueError("invalid guest draft fields")
    duration = values["duration_minutes"]
    if type(duration) is not int or not 1 <= duration <= 1440:
        raise ValueError("invalid guest draft duration")
    for key, minimum, maximum in (("current_a", 6, 32), ("energy_kwh", 0.1, 1000)):
        value = values[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum
            or abs(value * 10 - round(value * 10)) > 1e-8):
            raise ValueError("invalid guest draft value")
    return dict(values)


def read_snapshot(raw: object) -> dict[str, Any] | None:
    """Copy only the bounded public draft contract from a service response."""
    if (
        not isinstance(raw, dict)
        or raw.keys() != {"revision", "values", "storage_error", "status"}
        or not is_hex32(raw["revision"])
        or type(raw["storage_error"]) is not bool
        or raw["status"] not in DRAFT_STATES
    ):
        return None
    try:
        values = validate(raw["values"])
    except (ValueError, TypeError, OverflowError):
        return None
    return {
        "revision": raw["revision"], "values": values,
        "storage_error": raw["storage_error"], "status": raw["status"],
    }


class GuestDraft:
    """Persist only explicit edits; never save polling progress or arm a job."""

    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self.values = defaults()
        self.revision = secrets.token_hex(16)
        self.storage_error = False

    def load(self) -> None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(4097)
        except FileNotFoundError:
            return
        if len(raw) > 4096:
            raise ValueError("guest draft too large")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.keys() != {"version", "identity", "values"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["identity"] != self.identity
        ):
            raise ValueError("invalid saved guest draft")
        self.values = validate(value["values"])

    def save(self, values: dict[str, Any]) -> None:
        payload = json.dumps(
            {"version": 1, "identity": self.identity, "values": values}, allow_nan=False
        ).encode()
        replace_file(self.path, payload, prefix=".draft-")

    def snapshot(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "values": dict(self.values),
            "storage_error": self.storage_error,
        }
