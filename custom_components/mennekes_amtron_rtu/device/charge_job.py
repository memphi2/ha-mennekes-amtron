"""Bounded, session-scoped charging orders; no transport or HA dependencies.

A job supplies a time window and an additional-energy ceiling. It never grants
charging authorization or removes an independent energy/SoC stop. A restored
job cannot assume that the vehicle observed before shutdown is still present:
it continues only when the wallbox proves that the same plug-in went on.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .charge_reserve import ChargeReserve
from .decode import is_hex32
from .energy_goal import EnergyGoal

_BINDING = re.compile(r"[0-9a-f]{64}")
MAX_BOOST_MINUTES = 120
# How long an unreadable wallbox or plug state may last, as for Plug & Charge,
# before the order counts the connection as lost. One lost reply is routine.
UNKNOWN_GRACE = timedelta(seconds=60)
# The current register holds a float32: 10.1 A reads back as 10.1000004 A.
CURRENT_TOLERANCE_A = 0.05
# The wallbox's session clock has to keep pace with the wall clock to prove
# one plug-in: two minutes cover poll intervals and minute-resolution counts.
SESSION_TOLERANCE = timedelta(minutes=2)
# Float32 session energy is still the same session within this much.
_SESSION_ENERGY_TOLERANCE_KWH = 0.05
_UINT32 = 2**32 - 1
STATES = ("scheduled", "charging", "waiting_meter", "suspended", "reached", "finished", "stopped", "interrupted")
TERMINAL = frozenset({"reached", "finished", "stopped", "interrupted"})
REASONS = frozenset({
    "scheduled", "charging", "meter_unavailable", "energy_reached", "window_ended",
    "user_stop", "service_restarted", "connection_lost", "vehicle_changed",
    "goal_changed", "independent_stop", "meter_reset", "clock_changed", "storage_error",
})
_INTERRUPTIONS = frozenset({"service_restarted", "connection_lost", "vehicle_changed", "goal_changed", "independent_stop", "meter_reset", "clock_changed", "storage_error"})
# The app itself was away: only these may continue on proof of the same plug-in.
_SUSPENSIONS = frozenset({"service_restarted", "connection_lost"})
_FIELDS = frozenset({"id", "binding", "start_at", "finish_at", "current_a", "energy", "state", "reason", "scope_ended"})


def job_time(value: object) -> datetime:
    """Accept a bounded, timezone-aware ISO timestamp and normalize it to UTC."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid job time")
    try:
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("job time needs a timezone")
        return instant.astimezone(UTC)
    except (ValueError, OverflowError) as err:
        raise ValueError("invalid job time") from err


@dataclass(frozen=True, slots=True)
class SessionMark:
    """The wallbox's own record of one plug-in, which outlives the app.

    ``at`` is when ``duration_s`` and ``energy_kwh`` were read; ``count`` is the
    lifetime session counter and ``context`` a digest of the selected vehicle
    source and Plug & Charge policy, never a vehicle identity.
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
        return cls(job_time(raw["at"]), duration, float(energy), count, context)

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
            later.context == self.context and later.count == self.count
            and abs(later.duration_s - self.duration_s - elapsed) <= SESSION_TOLERANCE.total_seconds()
            and later.energy_kwh >= self.energy_kwh - _SESSION_ENERGY_TOLERANCE_KWH
        )


def _current(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 6 <= value <= 32:
        raise ValueError("job current must be between 6 and 32 A")
    return float(value)


@dataclass(slots=True)
class ChargeJob:
    """A single immutable request with a monotonic terminal decision.

    Ordinary progress stays in RAM. Only meaningful transitions need saving.
    A missing meter pauses; a recovered meter can continue the same known
    connection. Unplugging, a replug and clock rollback are terminal. An app
    restart or a lasting connection gap suspends the order: it holds its pause
    until the wallbox proves the same plug-in, and is interrupted otherwise.
    """

    identifier: str
    binding: str
    start_at: datetime
    finish_at: datetime
    current_a: float
    energy: EnergyGoal
    state: str = "scheduled"
    reason: str = "scheduled"
    scope_ended: bool = False
    _last_now: datetime | None = None
    _unknown_since: datetime | None = None
    boost_until: datetime | None = None
    kind: str = "scheduled"
    reserve: ChargeReserve | None = None
    # The latest wallbox session record of this connection. It refreshes in
    # RAM and is saved along with every decision.
    session: SessionMark | None = None
    _suspended_since: datetime | None = None

    @classmethod
    def create(cls, request: object, *, binding: str, total_kwh: object, now: datetime) -> ChargeJob:
        """Validate all inputs before arming the energy ceiling."""
        fields = {"id", "start_at", "finish_at", "current_a", "energy_kwh"}
        if not isinstance(request, dict) or request.keys() - {"kind", "reserve_kwh"} != fields:
            raise ValueError("invalid job request fields")
        kind = request.get("kind", "scheduled")
        if not isinstance(kind, str) or kind not in ("scheduled", "guest"):
            raise ValueError("invalid job kind")
        identifier = request["id"]
        if not is_hex32(identifier):
            raise ValueError("invalid job id")
        if not isinstance(binding, str) or _BINDING.fullmatch(binding) is None:
            raise ValueError("invalid job binding")
        current = _current(request["current_a"])
        start, finish = job_time(request["start_at"]), job_time(request["finish_at"])
        now = job_time(now.isoformat())
        if not now - timedelta(minutes=1) <= start < finish <= now + timedelta(days=7):
            raise ValueError("job window must end within seven days and start no more than one minute ago")
        if kind == "guest" and (start > now or finish - start > timedelta(days=1)):
            raise ValueError("guest window must start now and last at most 24 hours")
        energy = EnergyGoal()
        energy.set_target(request["energy_kwh"], total_kwh)
        if energy.target_kwh == 0:
            raise ValueError("job energy ceiling must be positive")
        reserve = ChargeReserve.create(request.get("reserve_kwh", 0), ceiling=energy.target_kwh, kind=kind, baseline=energy.baseline_kwh)
        return cls(identifier, binding, start, finish, current, energy, _last_now=now, kind=kind, reserve=reserve)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def stop_requested(self) -> bool:
        """A job outside its charging state supplies no resume permission."""
        return self.state != "charging"

    def allows_at(self, now: datetime, *, total_kwh: object = None) -> bool:
        """Reserve and boost open only earlier portions of the same ceiling."""
        return not self.terminal and self.state != "suspended" and now < self.finish_at and (
            now >= self.start_at or (self.boost_until is not None and now < self.boost_until)
            or (self.reserve is not None and self.reserve.needed(total_kwh, self.energy.baseline_kwh))
        )

    def set_boost(self, until: object, *, now: datetime) -> None:
        """Keep the original schedule, energy baseline and current ceiling."""
        end = job_time(until)
        now = job_time(now.isoformat())
        if self.kind == "guest" or self.terminal or self.state == "suspended" or self.scope_ended or not now < end <= min(
            self.start_at, self.finish_at, now + timedelta(minutes=MAX_BOOST_MINUTES),
        ):
            raise ValueError("boost must end before the original start and within two hours")
        self.boost_until = end

    def cancel_boost(self) -> None:
        """Return to the current plan; never rearm a terminal order."""
        self.boost_until = None

    def interrupt(self, reason: str) -> None:
        if reason not in _INTERRUPTIONS:
            raise ValueError("invalid job interruption reason")
        if not self.terminal:
            self.state, self.reason = "interrupted", reason
            self.cancel_boost()

    def suspend(self, reason: str) -> None:
        """Hold the pause until the wallbox proves the same plug-in.

        Without a session record nothing could prove it: interrupt directly.
        """
        if reason not in _SUSPENSIONS:
            raise ValueError("invalid job suspension reason")
        if self.terminal or self.state == "suspended":
            return
        if self.session is None:
            self.interrupt(reason)
            return
        self.state, self.reason = "suspended", reason
        self._suspended_since = self._unknown_since = None
        self.cancel_boost()

    def _reattach(
        self, *, now: datetime, present: bool | None, available: bool,
        scope: str | None, session: SessionMark | None,
    ) -> bool:
        """Continue a suspended order on the new connection, or end it.

        The same connection scope needs no proof. A lasting lack of evidence
        interrupts it, so Home Assistant regains control within a minute.
        """
        if present is False or now >= self.finish_at:
            self.scope_ended = present is False
            self.interrupt(self.reason)
            return False
        if not available:
            return False
        if self._suspended_since is None:
            self._suspended_since = now
        if present is True and scope is not None and (scope == self.binding or session is not None):
            if scope != self.binding and (
                self.session is None or session is None or not self.session.continues(session)
            ):
                self.interrupt(self.reason)
                return False
            self.binding, self.state, self.reason = scope, "scheduled", "scheduled"
            self._suspended_since = None
            return True
        if not timedelta(seconds=-5) <= now - self._suspended_since < UNKNOWN_GRACE:
            self.interrupt(self.reason)
        return False

    def stop(self) -> None:
        if not self.terminal:
            self.state, self.reason = "stopped", "user_stop"
            self.cancel_boost()

    def observe(
        self, *, now: datetime, present: bool | None, available: bool,
        matches: bool, total_kwh: object, meter_fresh: bool,
        independent_stop: bool = False, scope: str | None = None,
        session: SessionMark | None = None,
    ) -> None:
        """Compute intent only; the owner separately confirms actual writes."""
        if self.state == "suspended":
            if not self._reattach(
                now=job_time(now.isoformat()), present=present, available=available,
                scope=scope, session=session,
            ):
                return
            matches = True
        if present is False:
            self.scope_ended = True
        if self.terminal:
            return
        now = job_time(now.isoformat())
        if self._last_now is not None and now < self._last_now - timedelta(seconds=5):
            self.interrupt("clock_changed")
            return
        self._last_now = now
        if self.boost_until is not None and now >= self.boost_until:
            self.cancel_boost()
        if not available or present is None:
            # The owner fences every write meanwhile; only a lasting gap ends it.
            if self._unknown_since is None:
                self._unknown_since = now
            if now - self._unknown_since >= UNKNOWN_GRACE:
                self.suspend("connection_lost")
            return
        self._unknown_since = None
        if present is False or not matches:
            self.interrupt("vehicle_changed")
        elif independent_stop:
            self.interrupt("independent_stop")
        else:
            self.energy.observe(total_kwh, fresh=meter_fresh)
            if self.reserve is not None:
                self.reserve.observe(self.energy, total_kwh, fresh=meter_fresh)
            if self.energy.status == "reached":
                self.state, self.reason = "reached", "energy_reached"
            elif self.energy.status == "meter_reset":
                self.interrupt("meter_reset")
            elif now >= self.finish_at:
                self.state, self.reason = "finished", "window_ended"
            elif self.energy.status == "meter_unavailable":
                self.state, self.reason = "waiting_meter", "meter_unavailable"
            elif not self.allows_at(now, total_kwh=total_kwh):
                self.state, self.reason = "scheduled", "scheduled"
            else:
                self.state, self.reason = "charging", "charging"
        if self.terminal:
            self.cancel_boost()

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.identifier, "binding": self.binding,
            "start_at": self.start_at.isoformat(), "finish_at": self.finish_at.isoformat(),
            "current_a": self.current_a, "energy": self.energy.snapshot(),
            "state": self.state, "reason": self.reason, "scope_ended": self.scope_ended,
            **({"boost_until": self.boost_until.isoformat()} if self.boost_until is not None else {}),
            **({"kind": self.kind} if self.kind != "scheduled" else {}),
            **({"reserve": self.reserve.snapshot()} if self.reserve is not None else {}),
            **({"session": self.session.snapshot()} if self.session is not None else {}),
        }

    @classmethod
    def restore(cls, raw: object, *, restarted: bool = True) -> ChargeJob:
        """Reject corrupt state rather than silently recovering a resume grant."""
        if not isinstance(raw, dict) or raw.keys() - {"boost_until", "kind", "reserve", "session"} != _FIELDS:
            raise ValueError("invalid saved job fields")
        kind = raw.get("kind", "scheduled")
        if not isinstance(kind, str) or kind not in ("scheduled", "guest"):
            raise ValueError("invalid saved job kind")
        identifier, binding, state, reason = (raw[key] for key in ("id", "binding", "state", "reason"))
        if (
            not is_hex32(identifier)
            or not isinstance(binding, str) or _BINDING.fullmatch(binding) is None
            or not isinstance(state, str) or state not in STATES
            or not isinstance(reason, str) or reason not in REASONS
        ):
            raise ValueError("invalid saved job identity or state")
        start, finish = job_time(raw["start_at"]), job_time(raw["finish_at"])
        if not start < finish or finish - start > timedelta(days=7, minutes=1):
            raise ValueError("invalid saved job window")
        if kind == "guest" and finish - start > timedelta(days=1):
            raise ValueError("invalid saved guest window")
        energy = EnergyGoal.restore(raw["energy"])
        if energy.target_kwh == 0:
            raise ValueError("invalid saved job energy ceiling")
        if type(raw["scope_ended"]) is not bool:
            raise ValueError("invalid saved job scope")
        pairs = {"scheduled": "scheduled", "charging": "charging", "waiting_meter": "meter_unavailable", "reached": "energy_reached", "finished": "window_ended", "stopped": "user_stop"}
        expected_energy = {"scheduled": "active", "charging": "active", "waiting_meter": "meter_unavailable", "reached": "reached"}
        session = SessionMark.restore(raw["session"]) if "session" in raw else None
        if (
            (state in pairs and reason != pairs[state])
            or (state == "interrupted" and reason not in _INTERRUPTIONS)
            or (state == "suspended" and (
                reason not in _SUSPENSIONS or session is None
                or energy.status not in ("active", "meter_unavailable")
            ))
            or (state in expected_energy and energy.status != expected_energy[state])
            or (raw["scope_ended"] and state not in TERMINAL)
        ):
            raise ValueError("inconsistent saved job state")
        job = cls(identifier, binding, start, finish, _current(raw["current_a"]), energy, state, reason, raw["scope_ended"], kind=kind, session=session)
        if "reserve" in raw:
            job.reserve = ChargeReserve.restore(raw["reserve"], ceiling=energy.target_kwh, kind=kind, energy_status=energy.status, baseline=energy.baseline_kwh)
        if "boost_until" in raw:
            end = job_time(raw["boost_until"])
            if job.kind == "guest" or job.terminal or job.state == "suspended" or job.scope_ended or end > job.start_at:
                raise ValueError("invalid saved boost")
            job.boost_until = end
        if restarted:
            job.suspend("service_restarted")
        return job
