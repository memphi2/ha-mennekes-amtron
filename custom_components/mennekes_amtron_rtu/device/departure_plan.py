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
from .charging_plan import ChargePlan, plan_current, usable_capacity
from .client_errors import AmtronBusError
from .departure_model import (
    MIN_LEVEL_GAIN,
    POWER_WINDOW_SECONDS,
    DepartureModel,
)
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
    "minimum_percent", "detected",
})
_PLAN_KEYS = frozenset({
    "trip", "inputs", "anchor", "session", "planned_start", "plan_paused", "estimated_stop",
    "learned",
})
# Near a full battery the car tapers; that power says nothing about a charge.
_TAPER_LEVEL = 90.0
_FORECAST_STEP = 300
DEPARTURE_STATES = (
    "disabled", "waiting_for_target", "waiting_for_capacity", "waiting_for_soc",
    "waiting_for_device", "vehicle_unconfirmed", "read_only", "order_active", "queued",
    "paused_until_start", "minimum_level", "charging", "finishing", "unreachable", "late",
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
    service reported it; None means it confirms no car now. Below
    ``minimum_percent`` the car charges at once instead of waiting for the
    plan's start; zero switches that off. ``detected`` says the car itself
    reported the plug-in; without it the scope only repeats the selection.
    """

    vehicle: str
    scope: str | None = None
    capacity_kwh: float | None = None
    phases: int | None = None
    soc_percent: float | None = None
    soc_observed_at: datetime | None = None
    minimum_percent: float = 0.0
    detected: bool = False

    def __post_init__(self) -> None:
        if type(self.detected) is not bool:
            raise ValueError("invalid departure detection")
        object.__setattr__(self, "minimum_percent", percentage(self.minimum_percent))
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
            raw["minimum_percent"], raw["detected"],
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "vehicle": self.vehicle, "scope": self.scope,
            "capacity_kwh": self.capacity_kwh, "phases": self.phases,
            "soc_percent": self.soc_percent,
            "soc_observed_at": None if self.soc_observed_at is None else self.soc_observed_at.isoformat(),
            "minimum_percent": self.minimum_percent, "detected": self.detected,
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


_DIGEST = re.compile(r"[0-9a-f]{32}")
_STATE_KEYS = frozenset({
    "trip", "status", "planned_start", "estimated_soc", "inputs", "scope", "confirmed",
    "forecast",
})


@dataclass(frozen=True, slots=True)
class DepartureForecast:
    """When the plan reaches the target, and what it learned about the car."""

    finish_at: datetime
    level_at_departure: float
    efficiency: float
    full_power_kw: float | None

    @classmethod
    def restore(cls, raw: object) -> DepartureForecast:
        if not isinstance(raw, dict) or raw.keys() != {
            "finish_at", "level_at_departure", "efficiency", "full_power_kw",
        }:
            raise ValueError("invalid departure forecast fields")
        efficiency, power = raw["efficiency"], raw["full_power_kw"]
        for value in (efficiency, power):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0
            ):
                raise ValueError("invalid departure forecast value")
        if efficiency is None or efficiency > 1:
            raise ValueError("invalid departure forecast efficiency")
        return cls(
            instant(raw["finish_at"]), percentage(raw["level_at_departure"]), float(efficiency),
            None if power is None else float(power),
        )


@dataclass(frozen=True, slots=True)
class DepartureState:
    """The service's plan as Home Assistant shows it."""

    trip: TimedGoal
    status: str
    planned_start: datetime | None
    estimated_soc: float | None
    inputs: str | None
    scope: str | None
    confirmed: bool
    forecast: DepartureForecast | None = None

    @classmethod
    def restore(cls, raw: object) -> DepartureState:
        if not isinstance(raw, dict) or raw.keys() != _STATE_KEYS:
            raise ValueError("invalid departure plan fields")
        status, start, level = raw["status"], raw["planned_start"], raw["estimated_soc"]
        digest, scope, confirmed = raw["inputs"], raw["scope"], raw["confirmed"]
        if (
            status not in DEPARTURE_STATES or type(confirmed) is not bool
            or (digest is not None and (not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None))
            or (scope is not None and (not isinstance(scope, str) or _SCOPE.fullmatch(scope) is None))
        ):
            raise ValueError("invalid departure plan state")
        return cls(
            TimedGoal.restore(raw["trip"]), status, None if start is None else instant(start),
            None if level is None else percentage(level), digest, scope, confirmed,
            None if raw["forecast"] is None else DepartureForecast.restore(raw["forecast"]),
        )


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
        # What this wallbox's cars taught the plan; kept across trips.
        self.model = DepartureModel()
        # The report a learned efficiency counts from, within one plug-in.
        self._learn_from: SocAnchor | None = None
        # Since when the car charges at the plan's full current: meter time,
        # reading, phases and that current.
        self._full_from: tuple[float, float, int, float] | None = None
        self._at_full = False
        self.forecast: dict[str, Any] | None = None
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
        # Whether the wallbox showed the plan's pause as applied.
        self._pause_seen = False

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
        self.model = DepartureModel.restore(raw["learned"])
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
            "learned": self.model.snapshot(),
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
            "forecast": self.forecast,
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
                if deadline is None and self.estimated_stop and not withdraw:
                    # The car stands at its projected target: a cleared trip
                    # ends as reached, so the stop holds until the car leaves.
                    candidate.finish("reached")
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
            self._at_full, self.forecast = False, None
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
            self._learn_power(sample)
            await self._save()

    def _learn_power(self, s: DepartureSample) -> None:
        """Meter what the car really draws while the plan offers full current."""
        inputs = self.inputs
        if not self._at_full or inputs is None or s.phases is None or s.meter_at is None or s.total_kwh is None:
            self._full_from = None
            return
        start = self._full_from
        if start is None or start[2:] != (s.phases, s.max_current_a):
            self._full_from = (s.meter_at, s.total_kwh, s.phases, s.max_current_a)
            return
        seconds = s.meter_at - start[0]
        if seconds < POWER_WINDOW_SECONDS:
            return
        car = self.model.car(inputs.vehicle).with_power(
            s.phases, (s.total_kwh - start[1]) * 3600 / seconds, s.max_current_a,
        )
        self.model.learn(inputs.vehicle, car)
        self._full_from = (s.meter_at, s.total_kwh, s.phases, s.max_current_a)

    def _learn_efficiency(self, new: SocAnchor) -> None:
        """Compare two reports of the car with the energy metered between them."""
        inputs, base = self.inputs, self._learn_from
        if base is None or new.percent <= base.percent:
            # Only a report from this plug-in starts a comparison: an older
            # one may predate the drive here.
            here = bool(self._trail) and new.observed_at.timestamp() >= self._trail[0][0]
            self._learn_from = new if here else None
            return
        if (
            inputs is None or inputs.capacity_kwh is None or new.percent >= 100
            or new.percent - base.percent < MIN_LEVEL_GAIN
        ):
            return
        car = self.model.car(inputs.vehicle).with_charge(
            level_gain=new.percent - base.percent, capacity_kwh=inputs.capacity_kwh,
            metered_kwh=new.meter_kwh - base.meter_kwh,
        )
        self.model.learn(inputs.vehicle, car)
        self._learn_from = new

    def _forecast_for(
        self, plan: ChargePlan, s: DepartureSample, deadline: datetime, soc: float,
        efficiency: float, power: float | None,
    ) -> dict[str, Any]:
        """When the target is reached, and the level the car leaves with."""
        finish, level = deadline, s.target_percent
        if plan.status in ("unreachable", "late") and plan.full_hours:
            # Rounded, so that the forecast does not change with every tick.
            at = (s.now + timedelta(hours=plan.full_hours)).timestamp()
            finish = datetime.fromtimestamp(math.ceil(at / _FORECAST_STEP) * _FORECAST_STEP, UTC)
            hours = max(0.0, (deadline - s.now).total_seconds() / 3600)
            level = soc + (s.target_percent - soc) * min(1.0, hours / plan.full_hours)
        return {
            "finish_at": finish.isoformat(), "level_at_departure": round(level),
            "efficiency": round(efficiency, 2), "full_power_kw": None if power is None else round(power, 1),
        }

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

    def confirmed_vehicle(self, s: DepartureSample) -> tuple[str, str] | None:
        """The car Home Assistant detected at the plug-in of this sample."""
        inputs = self.inputs
        if (
            not self.ready or inputs is None or not inputs.detected or s.scope is None
            or self._binding(s) != "confirmed"
        ):
            return None
        return inputs.vehicle, s.scope

    def _unbind(self) -> None:
        # A provisional stop stays: a car that leaves at its projected target
        # still counts as reached at departure.
        self._session = self._bound_scope = None
        self.anchor = self._learn_from = None

    async def _evaluate(self, s: DepartureSample) -> None:
        if self.plan_paused and s.paused is True:
            self._pause_seen = True
        if self.plan_paused and s.paused is False and (self._command_current is not None or self._pause_seen):
            # The plan's own current ended its pause, or someone else ended
            # it after it had applied. A pause that is still queued, or a
            # start that was lost, has not: the pause stays this plan's to end.
            self.plan_paused = self._pause_seen = False
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
            if trip.finished != "reached":
                # Without a trip the plan's pause has nothing left to wait
                # for. At a reached target it holds until the car leaves.
                await self._rest(s)
            self._set_status(trip.finished or "disabled", s.now)
            return
        if deadline <= s.now:
            reason = trip.finished or ("reached" if self.estimated_stop else "late")
            await self._finish(reason, s, clear_deadline=True)
            if reason != "reached":
                await self._rest(s)
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
        vehicle = self.inputs.vehicle if self.inputs is not None else None
        phases = s.phases or (self.inputs.phases if self.inputs else None) or 3
        efficiency, power = self.model.efficiency(vehicle), self.model.car(vehicle).power(phases, s.max_current_a)
        plan = plan_current(
            now=s.now, deadline=deadline, soc_percent=soc, target_percent=s.target_percent,
            capacity_kwh=capacity, phases=phases, max_current_a=s.max_current_a,
            efficiency=efficiency, full_power_kw=power,
        )
        self.forecast = self._forecast_for(plan, s, deadline, soc, efficiency, power)
        minimum = self.inputs.minimum_percent if self.inputs is not None else 0.0
        step = plan_step(
            plan, self.planned_start, s.now,
            minimum_current_a=s.max_current_a if soc < minimum else None,
        )
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
        self._set_status(step.status, s.now)
        if step.current_a is None or s.paused is None:
            return
        if needs_current(step.current_a, paused=s.paused, applied=s.applied_a):
            await self._write(step.current_a, deadline)
        elif step.current_a == s.max_current_a and not s.paused and soc < _TAPER_LEVEL:
            self._at_full = True

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
        anchor = self.anchor
        # The same level reported again says nothing new: the energy metered
        # since its first report still counts.
        if anchor is not None and (observed <= anchor.observed_at or inputs.soc_percent == anchor.percent):
            return
        meter = self._meter_at(observed.timestamp(), s)
        if meter is not None:
            self.anchor = SocAnchor(inputs.soc_percent, observed, meter)
            self._learn_efficiency(self.anchor)

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
        gained = max(0.0, s.total_kwh - anchor.meter_kwh) * self.model.efficiency(inputs.vehicle)
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
        if current is None:
            self.plan_paused, self._pause_seen = True, False
        await self._apply(current, deadline)

    async def _cancel_command(self) -> None:
        if self._command_submitted:
            await self._cancel()
            self._command_submitted = False

    async def _rest(self, s: DepartureSample) -> None:
        await self._cancel_command()
        if not self.plan_paused:
            return
        # A goal's stop or an order owns the pause by now; leave it to them.
        if not (s.read_only or s.goal_stop or s.order_active):
            if s.pause_requested is None:
                # The pause intent is unknown for now; try again next time.
                return
            if s.pause_requested:
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
