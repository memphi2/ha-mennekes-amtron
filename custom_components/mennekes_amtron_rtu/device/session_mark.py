"""Wallbox evidence used to bind the vehicle planner to one physical connection."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_BINDING = re.compile(r"[0-9a-f]{64}")
SESSION_TOLERANCE = timedelta(minutes=2)
_UINT32 = 2**32 - 1

def session_time(value: object) -> datetime:
    """Accept a bounded, timezone-aware ISO timestamp and normalize it to UTC."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid session time")
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("session time needs a timezone")
        return instant.astimezone(UTC)
    except (ValueError, OverflowError) as err:
        raise ValueError("invalid session time") from err


@dataclass(frozen=True, slots=True)
class SessionMark:
    """The wallbox's own record of one plug-in, which outlives the app.

    ``at`` is when ``duration_s`` and ``energy_kwh`` were read; ``count`` is the
    lifetime session counter and ``context`` a digest of the physical connection context. The planner binds vehicle
    identity separately; this record never contains a VIN.
    """

    at: datetime
    duration_s: int
    energy_kwh: float
    count: int
    context: str

    @classmethod
    def restore(cls, raw: object) -> SessionMark:
        if not isinstance(raw, dict) or raw.keys() != {"at", "duration_s", "energy_kwh", "count", "context"}:
            raise ValueError("invalid saved session fields")
        duration, energy, count, context = (raw[key] for key in ("duration_s", "energy_kwh", "count", "context"))
        if (
            type(duration) is not int or not 0 <= duration <= _UINT32
            or type(count) is not int or not 0 <= count <= _UINT32
            or isinstance(energy, bool) or not isinstance(energy, (int, float))
            or not math.isfinite(energy) or energy < 0
            or not isinstance(context, str) or _BINDING.fullmatch(context) is None
        ):
            raise ValueError("invalid saved session")
        return cls(session_time(raw["at"]), duration, float(energy), count, context)

    def snapshot(self) -> dict[str, Any]:
        return {"at": self.at.isoformat(), "duration_s": self.duration_s,
                "energy_kwh": self.energy_kwh, "count": self.count, "context": self.context}

    def continues(self, later: SessionMark) -> bool:
        """A replug restarts the session clock and counts a new session.

        The clock must have run on with the wall clock, the counter must be
        unchanged and the session energy must not have restarted. Any doubt,
        including a clock that jumped meanwhile, is a different session.
        """
        elapsed = (later.at - self.at).total_seconds()
        return (
            elapsed >= 0 and later.duration_s >= self.duration_s
            and later.context == self.context and later.count == self.count
            and abs(later.duration_s - self.duration_s - elapsed) <= SESSION_TOLERANCE.total_seconds()
            and later.energy_kwh >= self.energy_kwh
        )
