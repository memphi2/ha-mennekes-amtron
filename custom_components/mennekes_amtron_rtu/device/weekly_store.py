"""Bounded recipes and monotonic consumed dates; never persist session consent."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .persistence import StorageCommitUncertainError
from .weekly_plan import WeeklyPlan

MAX_PLANS = 16
MAX_LEDGER = 128
MAX_WEEKLY_BYTES = 131072
_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class WeeklyBook:
    """Keep consumption tombstones when a recipe is edited or removed.

    A high-water local date prevents replay across same-day edits and clock
    rollback. Entries are never silently pruned; exhaustion fails closed.
    """

    plans: tuple[WeeklyPlan, ...] = ()
    consumed: tuple[tuple[str, date], ...] = ()

    @classmethod
    def parse(cls, raw: object) -> WeeklyBook:
        if not isinstance(raw, dict) or raw.keys() != {"plans", "consumed"}:
            raise ValueError("invalid weekly book fields")
        recipes, ledger = raw["plans"], raw["consumed"]
        if not isinstance(recipes, list) or len(recipes) > MAX_PLANS:
            raise ValueError("invalid weekly plan count")
        if not isinstance(ledger, dict) or len(ledger) > MAX_LEDGER:
            raise ValueError("invalid weekly ledger count")
        plans = tuple(WeeklyPlan.parse(item) for item in recipes)
        if len({p.identifier for p in plans}) != len(plans) or len({p.vehicle_profile for p in plans}) != len(plans):
            raise ValueError("duplicate weekly plan or vehicle profile")
        consumed = []
        for key, text in ledger.items():
            if not isinstance(key, str) or _ID.fullmatch(key) is None or not isinstance(text, str) or len(text) != 10:
                raise ValueError("invalid weekly consumed date")
            try:
                day = date.fromisoformat(text)
            except ValueError as err:
                raise ValueError("invalid weekly consumed date") from err
            if day.isoformat() != text:
                raise ValueError("invalid weekly consumed date")
            consumed.append((key, day))
        return cls(tuple(sorted(plans, key=lambda item: item.identifier)), tuple(sorted(consumed)))

    def snapshot(self) -> dict[str, Any]:
        return {"plans": [plan.snapshot() for plan in self.plans],
                "consumed": {key: day.isoformat() for key, day in self.consumed}}

    def plan(self, identifier: object) -> WeeklyPlan:
        for plan in self.plans:
            if plan.identifier == identifier:
                return plan
        raise ValueError("weekly plan not found")

    def put(self, plan: WeeklyPlan) -> WeeklyBook:
        raw = self.snapshot()
        raw["plans"] = [item for item in raw["plans"] if item["id"] != plan.identifier] + [plan.snapshot()]
        return self.parse(raw)

    def remove(self, identifier: object) -> WeeklyBook:
        self.plan(identifier)
        raw = self.snapshot()
        raw["plans"] = [item for item in raw["plans"] if item["id"] != identifier]
        return self.parse(raw)

    def used(self, identifier: str, day: date) -> bool:
        previous = dict(self.consumed).get(identifier)
        return previous is not None and day <= previous

    def reserve(self, identifier: str, day: date) -> WeeklyBook:
        self.plan(identifier)
        if self.used(identifier, day):
            raise ValueError("weekly occurrence already consumed")
        raw = self.snapshot()
        raw["consumed"][identifier] = day.isoformat()
        return self.parse(raw)


class WeeklyStore:
    """Commit consumption before job admission; uncertain commits fence repeats."""

    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: bytes | None = None

    def load(self) -> WeeklyBook:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_WEEKLY_BYTES + 1)
        except FileNotFoundError:
            book = WeeklyBook()
            self._last = self._encode(book)
            return book
        if len(raw) > MAX_WEEKLY_BYTES:
            raise ValueError("weekly storage exceeds size limit")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise ValueError("invalid weekly storage JSON") from err
        if (
            not isinstance(payload, dict) or payload.keys() != {"version", "identity", "book"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["identity"] != self.identity
        ):
            raise ValueError("weekly storage identity or format differs")
        book = WeeklyBook.parse(payload["book"])
        self._last = raw
        return book

    def _encode(self, book: WeeklyBook) -> bytes:
        return json.dumps({"version": 1, "identity": self.identity, "book": book.snapshot()},
                          allow_nan=False, sort_keys=True, separators=(",", ":")).encode()

    def save(self, book: WeeklyBook) -> None:
        payload = self._encode(book)
        if payload == self._last:
            return
        if len(payload) > MAX_WEEKLY_BYTES:
            raise ValueError("weekly storage exceeds size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".weekly-", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            self._last = None
            try:
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as err:
                raise StorageCommitUncertainError("weekly book replaced but directory sync failed") from err
            self._last = payload
        finally:
            temporary.unlink(missing_ok=True)
