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
STATES = ("scheduled", "charging", "waiting_meter", "reached", "finished", "stopped", "interrupted")
TERMINAL = frozenset({"reached", "finished", "stopped", "interrupted"})
REASONS = frozenset({
    "scheduled", "charging", "meter_unavailable", "energy_reached", "window_ended",
    "user_stop", "service_restarted", "connection_lost", "vehicle_changed",
    "goal_changed", "independent_stop", "meter_reset", "clock_changed", "storage_error",
})
_FIELDS = frozenset({"id", "binding", "start_at", "finish_at", "current_a", "energy", "state", "reason"})


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
    _last_now: datetime | None = None

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

    def interrupt(self, reason: str) -> None:
        if reason not in REASONS:
            raise ValueError("invalid job interruption reason")
        if not self.terminal:
            self.state, self.reason = "interrupted", reason

    def stop(self) -> None:
        if not self.terminal:
            self.state, self.reason = "stopped", "user_stop"

    def observe(
        self, *, now: datetime, present: bool | None, available: bool,
        matches: bool, total_kwh: object, meter_fresh: bool,
        independent_stop: bool = False,
    ) -> None:
        """Compute intent only; the owner separately confirms actual writes."""
        if self.terminal:
            return
        now = job_time(now.isoformat())
        if self._last_now is not None and now < self._last_now - timedelta(seconds=5):
            self.interrupt("clock_changed")
            return
        self._last_now = now
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
            elif now < self.start_at:
                self.state, self.reason = "scheduled", "scheduled"
            else:
                self.state, self.reason = "charging", "charging"

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.identifier, "binding": self.binding,
            "start_at": self.start_at.isoformat(), "finish_at": self.finish_at.isoformat(),
            "current_a": self.current_a, "energy": self.energy.snapshot(),
            "state": self.state, "reason": self.reason,
        }

    @classmethod
    def restore(cls, raw: object, *, restarted: bool = True) -> ChargeJob:
        """Reject corrupt state rather than silently recovering a resume grant."""
        if not isinstance(raw, dict) or raw.keys() != _FIELDS:
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
        job = cls(identifier, binding, start, finish, _current(raw["current_a"]), energy, state, reason)
        if restarted:
            job.interrupt("service_restarted")
        return job
