"""Bounded, observed charging sessions, independent of Home Assistant.

Only completed sessions are durable. Losing observation continuity must never
turn a partial charge into a measured full charge after a controller restart.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID, uuid4

from .data import WallboxData
from .enums import EvseState
from .registers import ENERGY_TOTAL, EVSE_STATE
from .vehicle import metered_vehicle_present

_MAX_EPOCH = 253402300799.0
_SESSION_KEYS = {
    "session_id", "connected_at", "disconnected_at", "started_after",
    "started_before", "ended_after", "ended_before", "wallbox_kwh",
}
# Plug-in and unplug change CP and EVSE state in separate polls, and a poll
# can fail. Such short unknown spans keep the cycle; a longer one could hide
# an unplug and a different car, so it still invalidates.
UNKNOWN_PRESENCE_GRACE_SECONDS = 120.0
# A slow scan still bridges one unknown poll: up to 2.5 intervals of 300 s.
_MAX_POLL_INTERVAL_SECONDS = 300.0
_NONCHARGING_STATES = frozenset({
    EvseState.EV_CONNECTED, EvseState.PRECONDITIONS_VALID, EvseState.READY_TO_CHARGE,
})


def _number(value: object, *, timestamp: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("session value must be numeric")
    try:
        result = float(value)
    except OverflowError as err:
        raise ValueError("session value must be finite") from err
    if not math.isfinite(result) or result < 0 or (timestamp and result > _MAX_EPOCH):
        raise ValueError("session value is out of range")
    return result


@dataclass(frozen=True, slots=True)
class CompletedSession:
    """A complete meter interval with observed bounds on actual charging.

    ``connected_at`` is the last confirmed absent sample before connection,
    i.e. the earliest possible connection time. ``disconnected_at`` is the
    first confirmed absent sample afterwards. Charge bounds are inclusive.
    """

    session_id: str
    connected_at: float
    disconnected_at: float
    started_after: float
    started_before: float
    ended_after: float
    ended_before: float
    wallbox_kwh: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def parse(cls, raw: object) -> CompletedSession:
        """Validate service or durable data before accepting it as a session."""
        if not isinstance(raw, dict) or raw.keys() != _SESSION_KEYS:
            raise ValueError("invalid completed session fields")
        identifier = raw["session_id"]
        if not isinstance(identifier, str) or len(identifier) != 36:
            raise ValueError("invalid completed session identifier")
        try:
            canonical = str(UUID(identifier))
        except ValueError as err:
            raise ValueError("invalid completed session identifier") from err
        if canonical != identifier:
            raise ValueError("noncanonical completed session identifier")
        times = {
            key: _number(raw[key], timestamp=True)
            for key in _SESSION_KEYS - {"session_id", "wallbox_kwh"}
        }
        if not (
            times["connected_at"] <= times["started_after"] <= times["started_before"]
            <= times["ended_after"] <= times["ended_before"] <= times["disconnected_at"]
        ):
            raise ValueError("inconsistent completed session time bounds")
        energy = _number(raw["wallbox_kwh"])
        if energy <= 0 or times["connected_at"] >= times["disconnected_at"]:
            raise ValueError("completed session needs positive energy and duration")
        return cls(identifier, wallbox_kwh=energy, **times)


@dataclass(slots=True)
class _Cycle:
    connected_at: float
    baseline_kwh: float | None
    last_noncharging_at: float
    valid: bool
    started_after: float | None = None
    started_before: float | None = None
    last_charging_at: float | None = None
    ended_after: float | None = None
    ended_before: float | None = None


class PresenceGap:
    """Bridge short unknown vehicle presence, shared by session and cost.

    The span runs from the last known poll. The allowance is two minutes, or
    two and a half observed poll intervals for a slow scan, so one unknown poll
    is bridged at any configured interval but a longer run is not.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._known_at: float | None = None
        self._interval: float | None = None
        self._unknown = False

    def observe(self, now: float, *, known: bool) -> bool:
        """Record one poll; return true when an unknown span became too long."""
        if self._known_at is not None and now < self._known_at:
            self.reset()  # The clock moved back; measure from here.
        expired = (
            (self._unknown or not known) and self._known_at is not None
            and now - self._known_at > max(
                UNKNOWN_PRESENCE_GRACE_SECONDS, 2.5 * (self._interval or 0.0),
            )
        )
        if known:
            if not self._unknown and self._known_at is not None:
                self._interval = min(now - self._known_at, _MAX_POLL_INTERVAL_SECONDS)
            self._known_at, self._unknown = now, False
        else:
            self._unknown = True
        return expired


class SessionHistory:
    """Retain one completed session and one transient connection cycle."""

    def __init__(self, cp_supported: bool = True) -> None:
        self._cp_supported = cp_supported
        self._latest: CompletedSession | None = None
        self._cycle: _Cycle | None = None
        self._last_at: float | None = None
        self._absent_at: float | None = None
        self._idle_energy: float | None = None
        self._last_energy: float | None = None
        self._gap = PresenceGap()
        # An unplugged cycle whose final meter is read in a later poll.
        self._closing: tuple[_Cycle, float] | None = None

    @property
    def latest(self) -> CompletedSession | None:
        return self._latest

    def _invalidate(self) -> None:
        if self._cycle is not None:
            self._cycle.valid = False
        if self._closing is not None:
            self._closing[0].valid = False
        self._absent_at = self._idle_energy = None

    def _presence(self, observed_at: float, *, known: bool) -> None:
        if self._gap.observe(observed_at, known=known):
            self._invalidate()

    def observe(self, data: WallboxData | None, *, now: float) -> bool:
        """Consume one poll; return true only if durable completed state changes."""
        previous = self._latest
        try:
            observed_at = _number(now, timestamp=True)
        except ValueError:
            self._invalidate()
            return False
        if self._last_at is not None and observed_at < self._last_at:
            self._invalidate()
            self._gap.reset()
            # Discard the overlapping cycle, then allow a fresh idle anchor on
            # the corrected clock rather than blocking until old time catches up.
            self._last_at = observed_at
            return False
        self._last_at = observed_at
        if data is None:
            self._presence(observed_at, known=False)
            return False
        present = metered_vehicle_present(data.get, cp_supported=self._cp_supported)

        previous_total = self._last_energy
        total: float | None = None
        if data.energy_read:
            try:
                total = _number(data.get(ENERGY_TOTAL.key))
            except ValueError:
                self._invalidate()
            else:
                if self._last_energy is not None and total < self._last_energy:
                    self._invalidate()
                if present is not None:
                    # Energy seen while presence is unknown must still count
                    # as accrued at the next unplugged reading.
                    self._last_energy = total
        self._presence(observed_at, known=present is not None)
        if present is None:
            return False

        if present is False:
            if self._cycle is not None and total is None and self._cycle.valid:
                # The meter is read on the next EVSE change or slow poll; no
                # energy flows meanwhile, so that later value is still final.
                self._closing = (self._cycle, observed_at)
                self._cycle = None
                self._idle_energy = None
            elif self._cycle is not None:
                self._latest = self._complete(self._cycle, total, observed_at)
                self._cycle = None
                self._idle_energy = None
            elif self._closing is not None and total is not None:
                cycle, unplugged_at = self._closing
                self._closing = None
                self._latest = self._complete(cycle, total, unplugged_at)
            elif total is not None and previous_total is not None and total > previous_total:
                # Energy accrued without an observed connection cycle. The
                # old completed record cannot represent the newest charge.
                self._latest = None
            self._absent_at = observed_at
            if data.energy_read:
                self._idle_energy = total
            return self._latest != previous

        if self._closing is not None:
            # Plugged in again before the final meter: the charge stays unmeasured.
            self._closing = None
            self._latest = None
        if self._cycle is None:
            anchored = self._absent_at is not None and self._idle_energy is not None
            self._cycle = _Cycle(
                connected_at=self._absent_at if self._absent_at is not None else observed_at,
                baseline_kwh=self._idle_energy,
                last_noncharging_at=self._absent_at if self._absent_at is not None else observed_at,
                valid=anchored,
            )
        cycle = self._cycle
        state = data.get(EVSE_STATE.key)
        if state == EvseState.CHARGING:
            if cycle.started_after is None:
                cycle.started_after = cycle.last_noncharging_at
                cycle.started_before = observed_at
            cycle.last_charging_at = observed_at
            cycle.ended_after = cycle.ended_before = None
        elif state in _NONCHARGING_STATES:
            if cycle.last_charging_at is not None and cycle.ended_before is None:
                cycle.ended_after = cycle.last_charging_at
                cycle.ended_before = observed_at
            if cycle.started_after is None:
                cycle.last_noncharging_at = observed_at
                if total is not None:
                    # Energy before any observed charging means a portion of
                    # this cycle was missed; a later segment is not its whole.
                    if cycle.baseline_kwh is not None and total > cycle.baseline_kwh:
                        cycle.valid = False
                    cycle.baseline_kwh = total
        # Error/service states with confirmed presence neither close the cycle
        # nor claim that charging stopped. A later known state bounds its end.
        return self._latest != previous

    @staticmethod
    def _complete(cycle: _Cycle, total: float | None, now: float) -> CompletedSession | None:
        if (
            not cycle.valid or total is None or cycle.baseline_kwh is None
            or cycle.started_after is None or cycle.started_before is None
            or cycle.last_charging_at is None
        ):
            return None
        energy = total - cycle.baseline_kwh
        candidate = CompletedSession(
            session_id=str(uuid4()), connected_at=cycle.connected_at, disconnected_at=now,
            started_after=cycle.started_after, started_before=cycle.started_before,
            ended_after=(cycle.ended_after if cycle.ended_after is not None else cycle.last_charging_at),
            ended_before=(cycle.ended_before if cycle.ended_before is not None else now),
            wallbox_kwh=energy,
        )
        try:
            return CompletedSession.parse(candidate.as_dict())
        except ValueError:
            return None

    def snapshot(self) -> dict[str, Any]:
        """No meter progress, current plug cycle or history list enters storage."""
        return {"version": 1, "latest": self._latest.as_dict() if self._latest else None}

    @classmethod
    def restore(cls, raw: object, *, cp_supported: bool = True) -> SessionHistory:
        """Restore completed data only, never claim observation across downtime."""
        if (
            not isinstance(raw, dict) or raw.keys() != {"version", "latest"}
            or type(raw["version"]) is not int or raw["version"] != 1
        ):
            raise ValueError("invalid charging session history")
        history = cls(cp_supported=cp_supported)
        if raw["latest"] is not None:
            history._latest = CompletedSession.parse(raw["latest"])
        return history
