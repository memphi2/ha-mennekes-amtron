"""A departure the service plans on its own: a car, a target and a time.

Home Assistant hands over the trip, the selected car with its capacity and
each charge level it learns, with the time the car reported it. The service
plans start and current with the same step as Home Assistant's direct mode.
While the car reports nothing, it projects the charge level from the metered
energy and stops there at the target; such a stop is provisional until the
car reports again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from bisect import bisect_left
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .charge_job import SessionMark
from .charging_plan import DEFAULT_BATTERY_EFFICIENCY, plan_current, usable_capacity
from .client_errors import AmtronBusError
from .departure_step import (
    MIN_WRITE_INTERVAL,
    keeps_start,
    needs_current,
    plan_step,
    same_command,
)
from .departure_store import DepartureStore
from .errors import DeviceError
from .persistence import finish_goal_save
from .soc_goal import percentage
from .timed_goal import TimedGoal

_LOGGER = logging.getLogger(__name__)

DEPARTURE_OWNER = "departure"
# A provisional stop holds while the charge level stays within this of the
# target; a real reading further below charges again.
SOC_HYSTERESIS = 1.0
# A car may report with a clock running slightly ahead of the service.
_CLOCK_SKEW = timedelta(minutes=5)
# Meter readings kept for one plug-in; beyond this every second one goes.
_TRAIL_LIMIT = 2048
_SCOPE = re.compile(r"[0-9a-f]{64}")
_INPUT_KEYS = frozenset({
    "vehicle", "scope", "capacity_kwh", "phases", "soc_percent", "soc_observed_at",
})
_PLAN_KEYS = frozenset({
    "trip", "inputs", "anchor", "session", "planned_start", "plan_paused", "estimated_stop",
})
DEPARTURE_STATES = (
    "disabled", "waiting_for_target", "waiting_for_capacity", "waiting_for_soc",
    "waiting_for_device", "vehicle_unconfirmed", "read_only", "order_active", "queued",
    "paused_until_start", "charging", "finishing", "unreachable", "late",
    "estimated_reached", "reached", "write_pending", "error",
)
Binding = Literal["confirmed", "unconfirmed", "unknown"]


def instant(raw: object) -> datetime:
    """Parse an ISO timestamp with its offset, as UTC."""
    if not isinstance(raw, str) or len(raw) > 64:
        raise ValueError("invalid timestamp")
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp needs a timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DepartureInputs:
    """What Home Assistant knows about the selected car, as one value.

    ``scope`` names the plug-in Home Assistant confirmed the car for, as the
    service reported it; None means it confirms no car now.
    """

    vehicle: str
    scope: str | None = None
    capacity_kwh: float | None = None
    phases: int | None = None
    soc_percent: float | None = None
    soc_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.vehicle, str) or not 1 <= len(self.vehicle) <= 512:
            raise ValueError("invalid departure vehicle")
        if self.scope is not None and (not isinstance(self.scope, str) or _SCOPE.fullmatch(self.scope) is None):
            raise ValueError("invalid departure scope")
        if self.capacity_kwh is not None:
            capacity = usable_capacity(self.capacity_kwh)
            if capacity is None:
                raise ValueError("invalid departure capacity")
            object.__setattr__(self, "capacity_kwh", capacity)
        if self.phases is not None and (type(self.phases) is not int or self.phases not in (1, 2, 3)):
            raise ValueError("invalid departure phases")
        if (self.soc_percent is None) != (self.soc_observed_at is None):
            raise ValueError("a charge level needs its report time")
        if self.soc_percent is not None:
            object.__setattr__(self, "soc_percent", percentage(self.soc_percent))
        observed = self.soc_observed_at
        if observed is not None:
            if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
                raise ValueError("the report time needs a timezone")
            object.__setattr__(self, "soc_observed_at", observed.astimezone(UTC))

    @classmethod
    def restore(cls, raw: object) -> DepartureInputs:
        if not isinstance(raw, dict) or raw.keys() != _INPUT_KEYS:
            raise ValueError("invalid departure input fields")
        observed = raw["soc_observed_at"]
        return cls(
            raw["vehicle"], raw["scope"], raw["capacity_kwh"], raw["phases"],
            raw["soc_percent"], None if observed is None else instant(observed),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "vehicle": self.vehicle, "scope": self.scope,
            "capacity_kwh": self.capacity_kwh, "phases": self.phases,
            "soc_percent": self.soc_percent,
            "soc_observed_at": None if self.soc_observed_at is None else self.soc_observed_at.isoformat(),
        }

    @property
    def digest(self) -> str:
        """Lets Home Assistant send the inputs only when they changed."""
        raw = json.dumps(self.snapshot(), allow_nan=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SocAnchor:
    """A charge level the car reported and the meter reading at that time."""

    percent: float
    observed_at: datetime
    meter_kwh: float

    @classmethod
    def restore(cls, raw: object) -> SocAnchor:
        if not isinstance(raw, dict) or raw.keys() != {"percent", "observed_at", "meter_kwh"}:
            raise ValueError("invalid charge level anchor fields")
        meter = raw["meter_kwh"]
        if isinstance(meter, bool) or not isinstance(meter, (int, float)) or not math.isfinite(meter) or meter < 0:
            raise ValueError("invalid charge level anchor meter")
        return cls(percentage(raw["percent"]), instant(raw["observed_at"]), float(meter))

    def snapshot(self) -> dict[str, Any]:
        return {"percent": self.percent, "observed_at": self.observed_at.isoformat(), "meter_kwh": self.meter_kwh}


@dataclass(frozen=True, slots=True)
class DepartureSample:
    """A passive view of the wallbox and its goals, taken for one decision."""

    now: datetime
    available: bool
    present: bool | None
    # This plug-in as an opaque digest, and the wallbox's record of it.
    scope: str | None
    session: SessionMark | None
    # The lifetime meter while fresh, and the wall time it was read.
    total_kwh: float | None
    meter_at: float | None
    # The current readback of this connection, None when not trustworthy.
    applied_a: float | None
    max_current_a: float
    paused: bool | None
    pause_requested: bool | None
    pending: bool
    read_only: bool
    vehicle: str | None
    target_percent: float
    goal_status: str
    # The target is the car's own limit, at which the car stops by itself.
    target_is_limit: bool
    energy_reached: bool
    goal_stop: bool
    order_active: bool
    # The phases the car draws on, None while the wallbox cannot tell.
    phases: int | None


class DepartureController:
    """Plan one trip for the confirmed car; own nothing but plan commands.

    Writes take the control's ordinary timed-command path under the service's
    command lock: manual and solar commands win at once, and the plan applies
    its current again only after its write interval.
    """

    def __init__(
        self, store: DepartureStore, sample: Callable[[], DepartureSample],
        apply: Callable[[float | None, datetime], Awaitable[None]],
        cancel: Callable[[], Awaitable[None]],
        resume: Callable[[], Awaitable[None]],
        *, monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.sample = sample
        self._apply = apply
        self._cancel = cancel
        self._resume = resume
        self._monotonic = monotonic
        self.wake = asyncio.Event()
        self.ready = False
        self.storage_error = False
        self.status = "disabled"
        self.scope: str | None = None
        self.confirmed = False
        self._lock = asyncio.Lock()
        self._closing = False
        self._durable: dict[str, Any] | None = None
        self._trail: list[tuple[float, float]] = []
        self._trail_scope: str | None = None
        self._estimate: float | None = None
        self._last_error: str | None = None
        self._reset()

    def _reset(self) -> None:
        self.trip = TimedGoal()
        self.inputs: DepartureInputs | None = None
        self.anchor: SocAnchor | None = None
        self.planned_start: datetime | None = None
        # This plan paused the wallbox, until its start or at its target.
        self.plan_paused = False
        # The projection, not a reading, reached the target.
        self.estimated_stop = False
        # The wallbox's record of the confirmed plug-in, to recognise it
        # after a restart or a lasting bus gap.
        self._session: SessionMark | None = None
        self._bound_scope: str | None = None
        self._reached_target: float | None = None
        self._last_write_at: float | None = None
        self._command_submitted = False
        self._command_current: float | None = None

    async def initialize(self) -> None:
        async with self._lock:
            try:
                raw = await asyncio.to_thread(self.store.load)
                if raw is not None:
                    self._restore(raw)
            except (OSError, ValueError, RecursionError) as err:
                # Home Assistant hands the trip over again; nothing is
                # planned until then.
                _LOGGER.warning("Saved departure plan unavailable (%s); waiting for Home Assistant", type(err).__name__)
                self._reset()
            self._durable = self._decision()
            self.ready = True

    def _restore(self, raw: dict[str, Any]) -> None:
        if raw.keys() != _PLAN_KEYS:
            raise ValueError("invalid departure plan fields")
        trip = TimedGoal.restore(raw["trip"])
        inputs = None if raw["inputs"] is None else DepartureInputs.restore(raw["inputs"])
        anchor = None if raw["anchor"] is None else SocAnchor.restore(raw["anchor"])
        session = None if raw["session"] is None else SessionMark.restore(raw["session"])
        start = None if raw["planned_start"] is None else instant(raw["planned_start"])
        if type(raw["plan_paused"]) is not bool or type(raw["estimated_stop"]) is not bool:
            raise ValueError("invalid departure plan flags")
        self.trip, self.inputs, self.anchor, self._session = trip, inputs, anchor, session
        self.planned_start = start
        self.plan_paused, self.estimated_stop = raw["plan_paused"], raw["estimated_stop"]

    def _state(self) -> dict[str, Any]:
        return {
            "trip": self.trip.snapshot(),
            "inputs": None if self.inputs is None else self.inputs.snapshot(),
            "anchor": None if self.anchor is None else self.anchor.snapshot(),
            "session": None if self._session is None else self._session.snapshot(),
            "planned_start": None if self.planned_start is None else self.planned_start.isoformat(),
            "plan_paused": self.plan_paused,
            "estimated_stop": self.estimated_stop,
        }

    def _decision(self) -> dict[str, Any]:
        """The saved part that matters; the session record refreshes in RAM."""
        return {**self._state(), "session": self._session is not None}

    async def _save(self) -> None:
        decision = self._decision()
        if decision == self._durable and not self.storage_error:
            return
        try:
            await finish_goal_save(asyncio.to_thread(self.store.save, self._state()))
        except (OSError, ValueError) as err:
            if not self.storage_error:
                _LOGGER.warning("Departure plan could not be saved (%s); it continues in memory", type(err).__name__)
            self.storage_error = True
            return
        self._durable = decision
        self.storage_error = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "trip": self.trip.snapshot(),
            "status": self.status,
            "planned_start": None if self.planned_start is None else self.planned_start.isoformat(),
            "estimated_soc": None if self._estimate is None else round(self._estimate, 1),
            "inputs": None if self.inputs is None else self.inputs.digest,
            "scope": self.scope,
            "confirmed": self.confirmed,
        }

    async def set_trip(self, deadline: datetime | None, source: str | None, *, withdraw: bool = False) -> None:
        """Take over Home Assistant's trip; ``withdraw`` also ends the plan's pause.

        Home Assistant withdraws when it moves on to another car, or none,
        which must not stay paused by this car's plan.
        """
        async with self._lock:
            if not self.ready or self._closing:
                raise DeviceError("service_unavailable")
            candidate = TimedGoal.restore(self.trip.snapshot())
            if candidate.set(deadline, source):
                await self._cancel_command()
                self.trip = candidate
                self._reached_target = self.planned_start = None
                self.estimated_stop = False
            if withdraw:
                self.inputs = None
                self._unbind()
            await self._save()
            if withdraw:
                await self._rest(self.sample())
                await self._save()
        self.wake.set()

    async def set_inputs(self, inputs: DepartureInputs) -> None:
        async with self._lock:
            if not self.ready or self._closing:
                raise DeviceError("service_unavailable")
            if self.inputs is not None and self.inputs.vehicle != inputs.vehicle:
                # Another car: its own reading anchors the projection.
                self._unbind()
            self.inputs = inputs
            await self._save()
        self.wake.set()

    def observe(self) -> None:
        """Follow the meter after each poll; decisions wait for the next tick."""
        if self.ready and not self._closing:
            self._observe(self.sample())

    def _observe(self, sample: DepartureSample) -> None:
        self.scope = sample.scope
        if sample.scope != self._trail_scope:
            # A new plug-in, or the same one after a gap: its energy starts a
            # new trail, and a reading before it counts from its start.
            self._trail = []
            self._trail_scope = sample.scope
        if sample.scope is None or sample.total_kwh is None or sample.meter_at is None:
            return
        if not self._trail or (sample.meter_at > self._trail[-1][0] and sample.total_kwh != self._trail[-1][1]):
            self._trail.append((sample.meter_at, sample.total_kwh))
            if len(self._trail) > _TRAIL_LIMIT:
                self._trail = self._trail[::2]

    async def tick(self) -> None:
        async with self._lock:
            if not self.ready or self._closing:
                return
            sample = self.sample()
            self._observe(sample)
            try:
                await self._evaluate(sample)
            except (AmtronBusError, DeviceError) as err:
                self.status = "write_pending"
                reason = getattr(err, "translation_key", None) or type(err).__name__
                if reason != self._last_error:
                    _LOGGER.warning("Departure plan command deferred (%s)", reason)
                    self._last_error = reason
            else:
                self._last_error = None
            await self._save()

    async def shutdown(self) -> None:
        self._closing = True
        async with self._lock:
            await self._save()

    def _binding(self, s: DepartureSample) -> Binding:
        inputs = self.inputs
        if inputs is None or inputs.scope is None or inputs.vehicle != s.vehicle or s.present is False:
            return "unconfirmed"
        if s.scope is None or s.present is not True:
            return "unknown"
        if s.scope in (inputs.scope, self._bound_scope):
            return "confirmed"
        if self._session is None:
            return "unconfirmed"
        if s.session is None:
            return "unknown"
        # Home Assistant confirmed this plug-in before a restart or a gap.
        return "confirmed" if self._session.continues(s.session) else "unconfirmed"

    def _unbind(self) -> None:
        # A provisional stop stays: a car that leaves at its projected target
        # still counts as reached at departure.
        self._session = self._bound_scope = None
        self.anchor = None

    async def _evaluate(self, s: DepartureSample) -> None:
        binding = self._binding(s)
        self.confirmed = binding == "confirmed"
        if binding == "confirmed":
            self._bound_scope = s.scope
            self._session = s.session or self._session
        elif binding == "unconfirmed":
            # Another car, or none: withdraw this car's plan, pause included.
            self._unbind()
            await self._rest(s)
        trip = self.trip
        deadline = trip.deadline
        if deadline is None:
            self._set_status(trip.finished or "disabled", s.now)
            return
        if deadline <= s.now:
            reason = trip.finished or ("reached" if self.estimated_stop else "late")
            await self._finish(reason, s, clear_deadline=True)
            return
        if binding != "confirmed":
            await self._cancel_command()
            if binding == "unknown":
                self._set_status("waiting_for_device", s.now, keep_start=True)
            else:
                self._set_status("vehicle_unconfirmed", s.now)
            return
        self._resolve_anchor(s)
        self._estimate = self._project(s)
        if trip.finished == "reached":
            if self._reached_target is None:
                self._reached_target = s.target_percent
            if self._target_raised(s):
                # A higher target plans the trip again. A charge level that
                # falls before departure, e.g. from preconditioning, does not.
                self.trip = TimedGoal()
                self.trip.set(deadline, trip.source)
                self._reached_target = self.planned_start = None
                await self._evaluate(s)
                return
        if trip.finished is not None:
            self._set_status(trip.finished, s.now)
            return
        if s.goal_status == "reached" or s.energy_reached:
            # The car reported the target, or the energy goal holds.
            await self._finish("reached", s)
            return
        if s.goal_status != "active" or s.target_percent <= 0:
            await self._wait("waiting_for_target", s)
            return
        capacity = self.inputs.capacity_kwh if self.inputs is not None else None
        if capacity is None:
            await self._wait("waiting_for_capacity", s)
            return
        # A poll, meter or charge level that drops out for a moment keeps the
        # plan's start: forgetting it would pause a charge that already runs.
        if not s.available or s.total_kwh is None:
            await self._wait("waiting_for_device", s, keep_start=True)
            return
        soc = self._estimate
        if soc is None:
            await self._wait("waiting_for_soc", s, keep_start=True)
            return
        if s.read_only:
            await self._wait("read_only", s)
            return
        if s.order_active:
            # An order from the app holds the wallbox until it ends.
            await self._wait("order_active", s, keep_start=True)
            return
        if soc >= s.target_percent or (self.estimated_stop and soc >= s.target_percent - SOC_HYSTERESIS):
            await self._stop_at_estimate(s, deadline)
            return
        self.estimated_stop = False
        if s.goal_stop:
            # Stop evidence awaiting the lock, or an order's stop, blocks
            # commands without proving that the target was reached.
            await self._wait("waiting_for_device", s)
            return
        plan = plan_current(
            now=s.now, deadline=deadline, soc_percent=soc, target_percent=s.target_percent,
            capacity_kwh=capacity, phases=s.phases or (self.inputs.phases if self.inputs else None) or 3,
            max_current_a=s.max_current_a,
        )
        step = plan_step(plan, self.planned_start, s.now)
        self.planned_start = step.planned_start
        if s.pending:
            await self._cancel_outdated(step.current_a)
            if self.sample().pending:
                self._set_status("queued", s.now)
                return
        if step.pauses:
            self._set_status("paused_until_start", s.now)
            if s.paused is False:
                await self._write(None, deadline)
            return
        self._set_status(plan.status, s.now)
        if plan.current_a is None or s.paused is None:
            return
        if needs_current(plan.current_a, paused=s.paused, applied=s.applied_a):
            await self._write(plan.current_a, deadline)

    async def _stop_at_estimate(self, s: DepartureSample, deadline: datetime) -> None:
        """Hold at the projected target until the car reports or departs."""
        self.estimated_stop = True
        if s.pending:
            await self._cancel_outdated(None)
        self._set_status("estimated_reached", s.now)
        # At its own limit the car stops by itself; pausing on an estimate
        # could only stop it short.
        if s.paused is False and not s.target_is_limit:
            # A stop is a new decision, not a repetition: it skips the write
            # interval once after a current of the plan.
            await self._write(None, deadline, force=self._command_current is not None)

    def _resolve_anchor(self, s: DepartureSample) -> None:
        inputs = self.inputs
        if inputs is None or inputs.soc_percent is None or inputs.soc_observed_at is None:
            return
        if inputs.soc_observed_at > s.now + _CLOCK_SKEW:
            return
        observed = min(inputs.soc_observed_at, s.now)
        if self.anchor is not None and observed <= self.anchor.observed_at:
            return
        meter = self._meter_at(observed.timestamp(), s)
        if meter is not None:
            self.anchor = SocAnchor(inputs.soc_percent, observed, meter)

    def _meter_at(self, at: float, s: DepartureSample) -> float | None:
        """The first reading of this plug-in at or after ``at``.

        A later reading counts slightly less energy since the report, so the
        projection errs towards charging more.
        """
        index = bisect_left(self._trail, at, key=lambda entry: entry[0])
        if index < len(self._trail):
            return self._trail[index][1]
        if s.total_kwh is not None and s.meter_at is not None and s.meter_at >= at:
            return s.total_kwh
        return None

    def _project(self, s: DepartureSample) -> float | None:
        anchor, inputs = self.anchor, self.inputs
        if anchor is None or inputs is None or inputs.capacity_kwh is None or s.total_kwh is None:
            return None
        gained = max(0.0, s.total_kwh - anchor.meter_kwh) * DEFAULT_BATTERY_EFFICIENCY
        return min(100.0, anchor.percent + gained / inputs.capacity_kwh * 100)

    def _target_raised(self, s: DepartureSample) -> bool:
        if (
            s.goal_status != "active" or self._reached_target is None
            or s.target_percent <= self._reached_target or s.energy_reached
        ):
            return False
        level = self._estimate if self._estimate is not None else (self.anchor.percent if self.anchor else None)
        return level is not None and level < s.target_percent

    async def _finish(self, reason: str, s: DepartureSample, *, clear_deadline: bool = False) -> None:
        candidate = TimedGoal.restore(self.trip.snapshot())
        candidate.finish(reason, clear_deadline=clear_deadline)
        await self._cancel_command()
        self.trip = candidate
        self.planned_start = None
        self.estimated_stop = False
        self._reached_target = s.target_percent if reason == "reached" else None
        self._set_status(reason, s.now)

    async def _cancel_outdated(self, current: float | None) -> None:
        # Fresh inputs can make a queued start or pause obsolete. Cancelling
        # checks the owner, so a newer manual request survives.
        if self._command_submitted and not same_command(self._command_current, current):
            await self._cancel_command()

    async def _write(self, current: float | None, deadline: datetime, *, force: bool = False) -> None:
        now = self._monotonic()
        if not force and self._last_write_at is not None and now - self._last_write_at < MIN_WRITE_INTERVAL:
            return
        # Record even uncertain writes, so reconnect churn cannot flood the bus.
        self._last_write_at = now
        self._command_submitted = True
        self._command_current = current
        self.plan_paused = current is None
        await self._apply(current, deadline)

    async def _cancel_command(self) -> None:
        if self._command_submitted:
            await self._cancel()
            self._command_submitted = False

    async def _rest(self, s: DepartureSample) -> None:
        await self._cancel_command()
        if self.plan_paused:
            # A goal's stop or an order owns the pause by now; leave it to them.
            if s.pause_requested is True and not (s.read_only or s.goal_stop or s.order_active):
                await self._resume()
                _LOGGER.info("Departure plan pause ended: the plan no longer applies to the car")
            self.plan_paused = False

    async def _wait(self, status: str, s: DepartureSample, *, keep_start: bool = False) -> None:
        # Keep an applied charge unchanged, but do not let an older queued
        # start run after the plan's target or input became unavailable.
        await self._cancel_command()
        self._set_status(status, s.now, keep_start=keep_start)

    def _set_status(self, status: str, now: datetime, *, keep_start: bool = False) -> None:
        # Only a start that already passed is kept; a future one is planned anew.
        if not keeps_start(status, self.planned_start, now, keep_start=keep_start):
            self.planned_start = None
        self.status = status
