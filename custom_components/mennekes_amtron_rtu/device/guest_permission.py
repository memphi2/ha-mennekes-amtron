"""Minimal restart evidence for a guest grant, not a separate charging order."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .session_mark import SessionMark, session_time


@dataclass(frozen=True, slots=True)
class GuestPermission:
    """Bind permission to wallbox evidence and verify the shared time survives."""

    session: SessionMark | None
    deadline: datetime | None

    def snapshot(self) -> dict[str, Any]:
        return {
            "session": self.session.snapshot() if self.session is not None else None,
            "deadline": self.deadline.isoformat() if self.deadline is not None else None,
        }

    @classmethod
    def restore(cls, raw: object) -> GuestPermission:
        if not isinstance(raw, dict) or raw.keys() != {"session", "deadline"}:
            raise ValueError("invalid guest permission")
        return cls(
            None if raw["session"] is None else SessionMark.restore(raw["session"]),
            None if raw["deadline"] is None else session_time(raw["deadline"]),
        )
