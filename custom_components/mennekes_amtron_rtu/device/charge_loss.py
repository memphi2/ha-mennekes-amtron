"""Compare explicitly associated charging energies with a bounded aggregate.

Vehicle energy must describe energy stored in the battery. Energy reported at
the vehicle's AC input measures a different boundary and cannot establish these
losses. The owner selects the association method; this model never claims that
an energy-value change proves which physical vehicle session it belongs to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .charging_session import CompletedSession

LOSS_ASSOCIATIONS = ("changed_value", "manual", "history")
LOSS_STATES = (
    "disabled", "waiting_for_session", "waiting_for_vehicle", "invalid", "matched",
)
_KEYS = {
    "source", "previous_wallbox_kwh", "previous_vehicle_kwh", "previous_count",
    "last_match", "current_session", "status",
}


def _number(value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("loss value must be a finite number")
    try:
        number = float(value)
    except OverflowError as err:
        raise ValueError("loss value must be finite") from err
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError("loss value is out of range")
    return number


@dataclass(frozen=True, slots=True)
class _Match:
    session: CompletedSession
    vehicle_kwh: float
    association: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "session": self.session.as_dict(), "vehicle_kwh": self.vehicle_kwh,
            "association": self.association,
        }

    @classmethod
    def restore(cls, raw: object) -> _Match:
        if not isinstance(raw, dict) or raw.keys() != {
            "session", "vehicle_kwh", "association",
        }:
            raise ValueError("invalid matched loss session")
        session = CompletedSession.parse(raw["session"])
        energy = _number(raw["vehicle_kwh"], positive=True)
        association = raw["association"]
        if energy > session.wallbox_kwh or association not in LOSS_ASSOCIATIONS:
            raise ValueError("inconsistent matched loss session")
        return cls(session, energy, association)


class LossStatistics:
    """Keep accepted energy sums and one correctable last match, without history.

    The current completed wallbox session can be newer than the last matched
    vehicle record. In that case the last-loss sensor is unknown while the
    average retains already accepted measurements. Unknown or invalid vehicle
    observations never create a zero-loss sample or erase the aggregate.
    """

    def __init__(self) -> None:
        self.source: str | None = None
        self.status = "disabled"
        self._previous_wallbox_kwh = 0.0
        self._previous_vehicle_kwh = 0.0
        self._previous_count = 0
        self._last_match: _Match | None = None
        self._current_session: CompletedSession | None = None

    @property
    def last_loss_percent(self) -> float | None:
        if self.status != "matched" or self._last_match is None:
            return None
        match = self._last_match
        return (1 - match.vehicle_kwh / match.session.wallbox_kwh) * 100

    @property
    def average_loss_percent(self) -> float | None:
        if self._last_match is None:
            return None
        wallbox = self._previous_wallbox_kwh + self._last_match.session.wallbox_kwh
        vehicle = self._previous_vehicle_kwh + self._last_match.vehicle_kwh
        return (1 - vehicle / wallbox) * 100

    @property
    def sample_count(self) -> int:
        return self._previous_count + int(self._last_match is not None)

    @property
    def last_session_id(self) -> str | None:
        return self._current_session.session_id if self._current_session is not None else None

    @property
    def association(self) -> str | None:
        return self._last_match.association if self.status == "matched" and self._last_match is not None else None

    @property
    def wallbox_kwh(self) -> float | None:
        return self._last_match.session.wallbox_kwh if self.status == "matched" and self._last_match is not None else None

    @property
    def vehicle_kwh(self) -> float | None:
        return self._last_match.vehicle_kwh if self.status == "matched" and self._last_match is not None else None

    def configure_source(self, source: str | None) -> bool:
        """Start a new aggregate when the selected vehicle/source changes."""
        if source is not None and (
            not isinstance(source, str) or not source.strip() or len(source) > 1024
        ):
            raise ValueError("invalid loss source")
        if source == self.source:
            return False
        self._previous_wallbox_kwh = 0.0
        self._previous_vehicle_kwh = 0.0
        self._previous_count = 0
        self._last_match = None
        self._current_session = None
        self.source = source
        self.status = "waiting_for_session" if source is not None else "disabled"
        return True

    def observe_session(self, session: CompletedSession | None) -> bool:
        """Select the newest complete session without claiming a vehicle match."""
        if self.source is None:
            return False
        if session is not None:
            session = CompletedSession.parse(session.as_dict())
            previous = self._current_session or (
                self._last_match.session if self._last_match is not None else None
            )
            if previous is not None:
                if session.session_id == previous.session_id:
                    if session != previous:
                        raise ValueError("loss session identifier changed its measurements")
                    if self._current_session is not None:
                        return False
                elif (
                    session.disconnected_at <= previous.disconnected_at
                    or session.connected_at < previous.disconnected_at
                ):
                    return False
        before = self.snapshot()
        self._current_session = session
        self.status = (
            "waiting_for_session" if session is None else
            "matched" if self._last_match is not None and self._last_match.session == session
            else "waiting_for_vehicle"
        )
        return before != self.snapshot()

    def apply(
        self, session: CompletedSession, vehicle_kwh: object, *, association: str,
    ) -> bool:
        """Accept a matching measurement once, or correct the latest match.

        Invalid/missing input makes the current result unknown but retains
        previous accepted sums. A correction of an older session is ignored.
        The return value describes durable changes, not whether a match exists.
        """
        if self.source is None:
            return False
        if association not in LOSS_ASSOCIATIONS:
            raise ValueError("invalid loss association")
        before = self.snapshot()
        self.observe_session(session)
        if self._current_session != session:
            return False
        if vehicle_kwh is None:
            self.status = "waiting_for_vehicle"
            return before != self.snapshot()
        try:
            energy = _number(vehicle_kwh, positive=True)
        except ValueError:
            self.status = "invalid"
            return before != self.snapshot()
        if energy > session.wallbox_kwh:
            self.status = "invalid"
            return before != self.snapshot()
        match = _Match(session, energy, association)
        previous_wallbox = self._previous_wallbox_kwh
        previous_vehicle = self._previous_vehicle_kwh
        previous_count = self._previous_count
        if self._last_match is not None and self._last_match.session.session_id != session.session_id:
            previous_wallbox += self._last_match.session.wallbox_kwh
            previous_vehicle += self._last_match.vehicle_kwh
            previous_count += 1
        if (
            not math.isfinite(previous_wallbox + session.wallbox_kwh)
            or not math.isfinite(previous_vehicle + energy)
            or previous_count >= 2**63 - 1
        ):
            self.status = "invalid"
            return before != self.snapshot()
        self._previous_wallbox_kwh = previous_wallbox
        self._previous_vehicle_kwh = previous_vehicle
        self._previous_count = previous_count
        self._last_match = match
        self.status = "matched"
        return before != self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """Serialize bounded settings, one session and accepted energy sums."""
        return {
            "source": self.source,
            "previous_wallbox_kwh": self._previous_wallbox_kwh,
            "previous_vehicle_kwh": self._previous_vehicle_kwh,
            "previous_count": self._previous_count,
            "last_match": self._last_match.snapshot() if self._last_match is not None else None,
            "current_session": (
                self._current_session.as_dict() if self._current_session is not None else None
            ),
            "status": self.status,
        }

    @classmethod
    def restore(cls, raw: object) -> LossStatistics:
        """Reject malformed aggregates instead of inventing a fresh average."""
        if not isinstance(raw, dict) or raw.keys() != _KEYS:
            raise ValueError("invalid loss statistics fields")
        result = cls()
        result.configure_source(raw["source"])
        wallbox = _number(raw["previous_wallbox_kwh"])
        vehicle = _number(raw["previous_vehicle_kwh"])
        count = raw["previous_count"]
        status = raw["status"]
        last = _Match.restore(raw["last_match"]) if raw["last_match"] is not None else None
        current = (
            CompletedSession.parse(raw["current_session"])
            if raw["current_session"] is not None else None
        )
        if (
            type(count) is not int or not 0 <= count < 2**63 - 1
            or vehicle > wallbox or (count == 0) != (wallbox == vehicle == 0)
            or (count > 0 and vehicle == 0)
            or (last is None and count != 0)
            or not isinstance(status, str) or status not in LOSS_STATES
            or (status == "disabled") != (result.source is None)
            or (result.source is None and (last is not None or current is not None))
            or (result.source is not None and (status == "waiting_for_session") != (current is None))
            or (status == "matched" and (last is None or last.session != current))
        ):
            raise ValueError("inconsistent loss statistics")
        if last is not None and (
            not math.isfinite(wallbox + last.session.wallbox_kwh)
            or not math.isfinite(vehicle + last.vehicle_kwh)
            or (
                current is not None and current != last.session
                and (
                    current.session_id == last.session.session_id
                    or current.disconnected_at <= last.session.disconnected_at
                    or current.connected_at < last.session.disconnected_at
                )
            )
        ):
            raise ValueError("inconsistent loss session order or totals")
        result._previous_wallbox_kwh = wallbox
        result._previous_vehicle_kwh = vehicle
        result._previous_count = count
        result._last_match = last
        result._current_session = current
        result.status = status
        return result
