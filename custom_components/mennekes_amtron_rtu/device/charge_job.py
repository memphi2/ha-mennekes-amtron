"""Bounded, session-scoped charging orders; no transport or HA dependencies.

A job supplies a time window and an additional-energy ceiling. It never grants
charging authorization or removes an independent energy/SoC stop. A restored
job cannot assume that the vehicle observed before shutdown is still present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .energy_goal import EnergyGoal

_JOB_ID = re.compile(r"[0-9a-f]{32}")
_BINDING = re.compile(r"[0-9a-f]{64}")
MAX_BOOST_MINUTES = 120
STATES = ("scheduled", "charging", "waiting_meter", "reached", "finished", "stopped", "interrupted")
TERMINAL = frozenset({"reached", "finished", "stopped", "interrupted"})
REASONS = frozenset({
    "scheduled", "charging", "meter_unavailable", "energy_reached", "window_ended",
    "user_stop", "service_restarted", "connection_lost", "vehicle_changed",
    "goal_changed", "independent_stop", "meter_reset", "clock_changed", "storage_error",
})
_INTERRUPTIONS = frozenset({"service_restarted", "connection_lost", "vehicle_changed", "goal_changed", "independent_stop", "meter_reset", "clock_changed", "storage_error"})
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


def _current(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 6 <= value <= 32:
        raise ValueError("job current must be between 6 and 32 A")
    return float(value)


@dataclass(slots=True)
class ChargeJob:
    """A single immutable request with a monotonic terminal decision.

    Ordinary progress stays in RAM. Only meaningful transitions need saving.
    A missing meter pauses; a recovered meter can continue the same known
    connection. Unplugging, connection ambiguity and clock rollback are terminal.
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
    boost_until: datetime | None = None

    @classmethod
    def create(cls, request: object, *, binding: str, total_kwh: object, now: datetime) -> ChargeJob:
        """Validate all inputs before arming the energy ceiling."""
        if not isinstance(request, dict) or request.keys() != {
            "id", "start_at", "finish_at", "current_a", "energy_kwh",
        }:
            raise ValueError("invalid job request fields")
        identifier = request["id"]
        if not isinstance(identifier, str) or _JOB_ID.fullmatch(identifier) is None:
            raise ValueError("invalid job id")
        if not isinstance(binding, str) or _BINDING.fullmatch(binding) is None:
            raise ValueError("invalid job binding")
        current = _current(request["current_a"])
        start, finish = job_time(request["start_at"]), job_time(request["finish_at"])
        now = job_time(now.isoformat())
        if not now - timedelta(minutes=1) <= start < finish <= now + timedelta(days=7):
            raise ValueError("job window must end within seven days and start no more than one minute ago")
        energy = EnergyGoal()
        energy.set_target(request["energy_kwh"], total_kwh)
        if energy.target_kwh == 0:
            raise ValueError("job energy ceiling must be positive")
        return cls(identifier, binding, start, finish, current, energy, _last_now=now)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def stop_requested(self) -> bool:
        """A job outside its charging state supplies no resume permission."""
        return self.state != "charging"

    def allows_at(self, now: datetime) -> bool:
        """A boost opens only an earlier part of the same bounded order."""
        return not self.terminal and now < self.finish_at and (
            now >= self.start_at or (self.boost_until is not None and now < self.boost_until)
        )

    def set_boost(self, until: object, *, now: datetime) -> None:
        """Keep the original schedule, energy baseline and current ceiling."""
        end = job_time(until)
        now = job_time(now.isoformat())
        if self.terminal or self.scope_ended or not now < end <= min(
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

    def stop(self) -> None:
        if not self.terminal:
            self.state, self.reason = "stopped", "user_stop"
            self.cancel_boost()

    def observe(
        self, *, now: datetime, present: bool | None, available: bool,
        matches: bool, total_kwh: object, meter_fresh: bool,
        independent_stop: bool = False,
    ) -> None:
        """Compute intent only; the owner separately confirms actual writes."""
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
            self.interrupt("connection_lost")
        elif present is False or not matches:
            self.interrupt("vehicle_changed")
        elif independent_stop:
            self.interrupt("independent_stop")
        else:
            self.energy.observe(total_kwh, fresh=meter_fresh)
            if self.energy.status == "reached":
                self.state, self.reason = "reached", "energy_reached"
            elif self.energy.status == "meter_reset":
                self.interrupt("meter_reset")
            elif now >= self.finish_at:
                self.state, self.reason = "finished", "window_ended"
            elif self.energy.status == "meter_unavailable":
                self.state, self.reason = "waiting_meter", "meter_unavailable"
            elif not self.allows_at(now):
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
        }

    @classmethod
    def restore(cls, raw: object, *, restarted: bool = True) -> ChargeJob:
        """Reject corrupt state rather than silently recovering a resume grant."""
        if not isinstance(raw, dict) or raw.keys() not in (_FIELDS, _FIELDS | {"boost_until"}):
            raise ValueError("invalid saved job fields")
        identifier, binding, state, reason = (raw[key] for key in ("id", "binding", "state", "reason"))
        if (
            not isinstance(identifier, str) or _JOB_ID.fullmatch(identifier) is None
            or not isinstance(binding, str) or _BINDING.fullmatch(binding) is None
            or not isinstance(state, str) or state not in STATES
            or not isinstance(reason, str) or reason not in REASONS
        ):
            raise ValueError("invalid saved job identity or state")
        start, finish = job_time(raw["start_at"]), job_time(raw["finish_at"])
        if not start < finish or finish - start > timedelta(days=7, minutes=1):
            raise ValueError("invalid saved job window")
        energy = EnergyGoal.restore(raw["energy"])
        if energy.target_kwh == 0:
            raise ValueError("invalid saved job energy ceiling")
        if type(raw["scope_ended"]) is not bool:
            raise ValueError("invalid saved job scope")
        pairs = {"scheduled": "scheduled", "charging": "charging", "waiting_meter": "meter_unavailable", "reached": "energy_reached", "finished": "window_ended", "stopped": "user_stop"}
        expected_energy = {"scheduled": "active", "charging": "active", "waiting_meter": "meter_unavailable", "reached": "reached"}
        if (
            (state in pairs and reason != pairs[state])
            or (state == "interrupted" and reason not in _INTERRUPTIONS)
            or (state in expected_energy and energy.status != expected_energy[state])
            or (raw["scope_ended"] and state not in TERMINAL)
        ):
            raise ValueError("inconsistent saved job state")
        job = cls(identifier, binding, start, finish, _current(raw["current_a"]), energy, state, reason, raw["scope_ended"])
        if "boost_until" in raw:
            end = job_time(raw["boost_until"])
            if job.terminal or job.scope_ended or end > job.start_at:
                raise ValueError("invalid saved boost")
            job.boost_until = end
        if restarted:
            job.interrupt("service_restarted")
        return job
