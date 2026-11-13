"""One departure time, retained until explicitly replaced or cleared."""

from __future__ import annotations

from datetime import UTC, datetime


def matches_departure_source(source: str | None, entity_id: str, value: datetime) -> bool:
    """Recognize the same trip, including saved markers with non-UTC offsets."""
    prefix = f"entity:{entity_id}:"
    if source is None or not source.startswith(prefix):
        return False
    try:
        previous = datetime.fromisoformat(source[len(prefix):])
        if (
            previous.tzinfo is None or previous.utcoffset() is None
            or value.tzinfo is None or value.utcoffset() is None
        ):
            return False
        return previous.astimezone(UTC) == value.astimezone(UTC)
    except (OverflowError, ValueError):
        return False


class TimedGoal:
    """Persist the selected next trip without a rolling daily schedule."""

    def __init__(self) -> None:
        self.deadline: datetime | None = None
        self.source: str | None = None
        self.finished: str | None = None

    def set(self, deadline: datetime | None, source: str | None = None) -> bool:
        if deadline is not None and (
            deadline.tzinfo is None or deadline.utcoffset() is None
        ):
            raise ValueError("departure time needs a timezone")
        if source is not None and (not source or len(source) > 512):
            raise ValueError("invalid departure source")
        before = self.snapshot()
        if deadline == self.deadline and source == self.source and self.finished is None:
            return False
        self.deadline = deadline
        # Retain the source marker when cleared. Otherwise an unchanged HA
        # timestamp would immediately arm the cancelled trip again.
        self.source = source
        self.finished = None
        return self.snapshot() != before

    def finish(self, reason: str, *, clear_deadline: bool = False) -> None:
        """Consume one trip while remembering which source must not rearm it."""
        if reason not in ("reached", "late"):
            raise ValueError("invalid departure completion")
        if clear_deadline or reason == "late":
            self.deadline = None
        self.finished = reason

    def snapshot(self) -> dict[str, str | None]:
        return {
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "source": self.source,
            "finished": self.finished,
        }

    @classmethod
    def restore(cls, raw: object) -> TimedGoal:
        if not isinstance(raw, dict) or raw.keys() != {"deadline", "source", "finished"}:
            raise ValueError("invalid departure goal fields")
        deadline_raw, source = raw["deadline"], raw["source"]
        if deadline_raw is not None and not isinstance(deadline_raw, str):
            raise ValueError("invalid departure time")
        if source is not None and not isinstance(source, str):
            raise ValueError("invalid departure source")
        goal = cls()
        try:
            goal.set(datetime.fromisoformat(deadline_raw) if deadline_raw else None, source)
            finished = raw["finished"]
            if finished is not None:
                if deadline_raw is not None and finished != "reached":
                    raise ValueError("completed departure still has a deadline")
                goal.finish(finished)
        except (TypeError, ValueError) as err:
            raise ValueError("invalid departure goal") from err
        return goal
