"""Bounded atomic storage for the service's departure plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .persistence import replace_file

MAX_PLAN_BYTES = 8192


class DepartureStore:
    """One file per wallbox; the controller validates what it holds."""

    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: bytes | None = None

    def load(self) -> dict[str, Any] | None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_PLAN_BYTES + 1)
        except FileNotFoundError:
            self._last = self._encode(None)
            return None
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("saved departure plan exceeds size limit")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise ValueError("invalid departure plan JSON") from err
        if (
            not isinstance(payload, dict) or payload.keys() != {"version", "identity", "plan"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["identity"] != self.identity
            or not isinstance(payload["plan"], (dict, type(None)))
        ):
            raise ValueError("saved departure plan identity or format differs")
        self._last = raw
        return payload["plan"]

    def _encode(self, plan: dict[str, Any] | None) -> bytes:
        return json.dumps(
            {"version": 1, "identity": self.identity, "plan": plan},
            allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode()

    def save(self, plan: dict[str, Any] | None) -> None:
        payload = self._encode(plan)
        if payload == self._last:
            return
        if len(payload) > MAX_PLAN_BYTES:
            raise ValueError("departure plan exceeds size limit")
        # An uncertain commit writes again next time.
        self._last = None
        replace_file(self.path, payload, prefix=".departure-")
        self._last = payload
