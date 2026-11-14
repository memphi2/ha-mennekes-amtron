"""One bounded durable job draft, without any permission to charge."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .charge_job import job_time
from .decode import is_hex32
from .persistence import replace_file

DRAFT_STATES = (
    "needs_finish",
    "ready",
    "job_exists",
    "job_not_permitted",
    "job_current_exceeds_cap",
    "job_preview_expired",
    "job_storage_unavailable",
    "invalid_job_request",
    "job_stop_unconfirmed",
)

FIELDS = frozenset({"start_at", "finish_at", "current_a", "energy_kwh", "reserve_kwh"})


def validate(values: object) -> dict[str, Any]:
    """Allow incomplete/expired dates; creation performs live time checks."""
    if not isinstance(values, dict) or values.keys() != FIELDS:
        raise ValueError("invalid job draft fields")
    result = dict(values)
    for key in ("start_at", "finish_at"):
        if result[key] is not None:
            result[key] = job_time(result[key]).isoformat()
    for key, minimum, maximum in (
        ("current_a", 6, 32),
        ("energy_kwh", 0.1, 1000),
        ("reserve_kwh", 0, 1000),
    ):
        value = result[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not minimum <= value <= maximum
            or abs(value * 10 - round(value * 10)) > 1e-8
        ):
            raise ValueError("invalid job draft value")
    if result["reserve_kwh"] > result["energy_kwh"]:
        raise ValueError("draft reserve exceeds energy")
    return result


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


class JobDraft:
    """Persist only explicit edits; never save polling progress or arm a job."""

    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self.values: dict[str, Any] = {
            "start_at": None,
            "finish_at": None,
            "current_a": 6.0,
            "energy_kwh": 10.0,
            "reserve_kwh": 0.0,
        }
        self.revision = secrets.token_hex(16)
        self.storage_error = False

    def load(self) -> None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(4097)
        except FileNotFoundError:
            return
        if len(raw) > 4096:
            raise ValueError("job draft too large")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.keys() != {"version", "identity", "values"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["identity"] != self.identity
        ):
            raise ValueError("invalid saved job draft")
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
