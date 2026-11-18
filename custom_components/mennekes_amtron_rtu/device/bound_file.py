"""Bounded atomic storage of one JSON value, bound to one wallbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from .persistence import replace_file


class BoundFile:
    """One file per wallbox; whoever owns the value validates what it holds."""

    field: ClassVar[str]
    what: ClassVar[str]
    max_bytes: ClassVar[int]
    prefix: ClassVar[str]

    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: bytes | None = None

    def load(self) -> dict[str, Any] | None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(self.max_bytes + 1)
        except FileNotFoundError:
            self._last = self._encode(None)
            return None
        if len(raw) > self.max_bytes:
            raise ValueError(f"saved {self.what} exceeds size limit")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise ValueError(f"invalid {self.what} JSON") from err
        if (
            not isinstance(payload, dict) or payload.keys() != {"version", "identity", self.field}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["identity"] != self.identity
            or not isinstance(payload[self.field], (dict, type(None)))
        ):
            raise ValueError(f"saved {self.what} identity or format differs")
        self._last = raw
        value: dict[str, Any] | None = payload[self.field]
        return value

    def _encode(self, value: dict[str, Any] | None) -> bytes:
        return json.dumps(
            {"version": 1, "identity": self.identity, self.field: value},
            allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode()

    def save(self, value: dict[str, Any] | None) -> None:
        payload = self._encode(value)
        if payload == self._last:
            return
        if len(payload) > self.max_bytes:
            raise ValueError(f"{self.what} exceeds size limit")
        # An uncertain commit writes again next time.
        self._last = None
        replace_file(self.path, payload, prefix=self.prefix)
        self._last = payload
