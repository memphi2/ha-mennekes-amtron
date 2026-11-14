"""One answer to who controls the charge, why it waits and what comes next.

Read from the state the controllers already publish. Nothing here decides or
commands: the app's overview and Home Assistant show the same summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CONTROLLERS = (
    "unavailable", "guest", "departure_plan", "direct",
)
WAITING = (
    "none", "wallbox_unavailable", "no_vehicle", "release_off", "plug_charge_waiting",
    "meter_unavailable",
    "vehicle_unconfirmed", "until_planned_start",
    "estimated_target_reached", "waiting_for_device", "waiting_for_soc",
    "waiting_for_capacity", "waiting_for_target", "read_only", "command_pending",
    "target_reached", "paused",
)
EVENTS = (
    "none", "queued_command", "plan_start",
    "target_reached", "departure",
)
VEHICLES = ("unknown", "none", "connected", "confirmed")
TARGETS = ("none", "level", "energy")

# The plan's own status where it names why the car does not charge yet.
_PLAN_WAITS = {
    "vehicle_unconfirmed": "vehicle_unconfirmed",
    "paused_until_start": "until_planned_start",
    "estimated_reached": "estimated_target_reached",
    "waiting_for_device": "waiting_for_device",
    "waiting_for_soc": "waiting_for_soc",
    "waiting_for_capacity": "waiting_for_capacity",
    "waiting_for_target": "waiting_for_target",
    "read_only": "read_only",
    "queued": "command_pending",
    "write_pending": "command_pending",
}
_PLAN_STARTS = frozenset({"paused_until_start", "minimum_level"})
_PLAN_CHARGES = frozenset({"charging", "finishing", "unreachable", "late"})
_KEYS = frozenset({"controller", "waiting", "next", "target", "vehicle"})


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 < value < 1e6 else None


def _event(name: str, at: object) -> dict[str, Any]:
    moment = _time(at)
    # An event without its time says nothing a reader could act on.
    return {"event": name if moment is not None else "none", "at": None if moment is None else moment.isoformat()}


def _target(kind: str, value: object, deadline: object = None) -> dict[str, Any]:
    number, moment = _positive(value), _time(deadline)
    if number is None:
        return {"kind": "none", "value": None, "deadline": None}
    return {"kind": kind, "value": number, "deadline": None if moment is None else moment.isoformat()}


def summarize(
    *, available: bool, present: bool | None, control: Mapping[str, Any],
    departure: Mapping[str, Any] | None,
    goal_stop: bool = False,
) -> dict[str, Any]:
    """Summarize the controllers' published state.

    ``departure`` is the vehicle plan snapshot, or None while it loads.
    """
    plan = _map(departure)
    trip = _map(plan.get("trip"))
    soc, energy = _map(control.get("soc_goal")), _map(control.get("energy_goal"))
    status = plan.get("status")
    guest = control.get("guest_mode") is True
    planning = trip.get("deadline") is not None and trip.get("finished") is None

    waits: str | None = None
    following = _event("none", None)
    target = _target("none", None)
    if guest:
        controller = "guest"
        target = _target("energy", energy.get("target_kwh"), trip.get("deadline"))
        following = _event("departure", trip.get("deadline"))
    elif planning:
        controller = "departure_plan"
        target = _target("level", soc.get("target_percent"), trip.get("deadline"))
        waits = _PLAN_WAITS.get(status) if isinstance(status, str) else None
        forecast = _map(plan.get("forecast"))
        if status in _PLAN_STARTS:
            following = _event("plan_start", plan.get("planned_start"))
        elif status in _PLAN_CHARGES and forecast:
            following = _event("target_reached", forecast.get("finish_at"))
        if following["event"] == "none":
            following = _event("departure", trip.get("deadline"))
    else:
        controller = "direct"
        if soc.get("status") in ("active", "reached"):
            target = _target("level", soc.get("target_percent"))
        if target["kind"] == "none":
            target = _target("energy", energy.get("target_kwh"))

    gate = _map(control.get("plug_charge"))
    if not available:
        controller, waiting = "unavailable", "wallbox_unavailable"
    elif present is False:
        waiting = "no_vehicle"
    elif control.get("guest_confirmation_required") is True:
        waiting = "vehicle_unconfirmed"
    elif control.get("charging_release_requested") is False:
        waiting = "release_off"
    elif gate.get("enabled") is True and not (
        gate.get("authorized") is True or control.get("plug_charge_manual") is True
    ):
        waiting = "plug_charge_waiting"
    elif energy.get("status") in ("meter_unavailable", "meter_reset"):
        waiting = "meter_unavailable"
    elif waits is not None:
        waiting = waits
    elif goal_stop or (not guest and soc.get("status") == "reached") or energy.get("status") == "reached":
        waiting = "target_reached"
    elif control.get("pending_action") is not None:
        waiting = "command_pending"
    elif control.get("pause_requested") is True:
        waiting = "paused"
    else:
        waiting = "none"

    queued = _event("queued_command", control.get("pending_until"))
    if control.get("pending_action") is not None and queued["event"] != "none":
        planned = _time(following["at"])
        if planned is None or datetime.fromisoformat(queued["at"]) <= planned:
            following = queued

    if present is False:
        vehicle = "none"
    elif present is None or not available:
        vehicle = "unknown"
    else:
        vehicle = "confirmed" if plan.get("confirmed") is True else "connected"
    return {
        "controller": controller, "waiting": waiting, "next": following, "target": target,
        "vehicle": vehicle,
    }


@dataclass(frozen=True, slots=True)
class Operation:
    """The service's summary as Home Assistant shows it."""

    controller: str
    waiting: str
    next_event: str
    next_at: datetime | None
    target_kind: str
    target_value: float | None
    target_deadline: datetime | None
    vehicle: str

    @classmethod
    def restore(cls, raw: object) -> Operation:
        if not isinstance(raw, dict) or raw.keys() != _KEYS:
            raise ValueError("invalid operation fields")
        following, target = raw["next"], raw["target"]
        if not isinstance(following, dict) or following.keys() != {"event", "at"}:
            raise ValueError("invalid operation next step")
        if not isinstance(target, dict) or target.keys() != {"kind", "value", "deadline"}:
            raise ValueError("invalid operation target")
        for value, allowed in (
            (raw["controller"], CONTROLLERS), (raw["waiting"], WAITING),
            (following["event"], EVENTS), (target["kind"], TARGETS), (raw["vehicle"], VEHICLES),
        ):
            if not isinstance(value, str) or value not in allowed:
                raise ValueError("invalid operation value")
        at, deadline = (
            None if value is None else _time(value) for value in (following["at"], target["deadline"])
        )
        if (following["at"] is None) != (at is None) or (following["event"] == "none") != (at is None):
            raise ValueError("invalid operation next time")
        if (target["deadline"] is None) != (deadline is None):
            raise ValueError("invalid operation deadline")
        amount = None if target["value"] is None else _positive(target["value"])
        if (target["kind"] == "none") != (amount is None) or (target["value"] is None) != (amount is None):
            raise ValueError("invalid operation target value")
        if target["kind"] == "none" and deadline is not None:
            raise ValueError("invalid operation deadline")
        return cls(
            raw["controller"], raw["waiting"], following["event"], at,
            target["kind"], amount, deadline, raw["vehicle"],
        )
