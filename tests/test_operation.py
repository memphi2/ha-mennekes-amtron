"""The service's summary of who controls the charge, why it waits and what comes next."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.mennekes_amtron_rtu.device.operation import (
    Operation,
    summarize,
)

START = "2026-11-14T02:00:00+00:00"
END = "2026-11-14T06:00:00+00:00"


def _control(**changes: Any) -> dict[str, Any]:
    return {
        "soc_goal": {"source": None, "target_percent": 0.0, "maximum_percent": None, "status": "disabled"},
        "energy_goal": {"target_kwh": None, "baseline_kwh": None, "status": "disabled"},
        "plug_charge": {"enabled": False, "authorized": False},
        "plug_charge_manual": False,
        "charging_release_requested": True,
        "pause_requested": False,
        "pending_action": None,
        "pending_until": None,
        **changes,
    }


def _plan(status: str = "paused_until_start", **changes: Any) -> dict[str, Any]:
    return {
        "trip": {"deadline": END, "source": "manual", "finished": None},
        "status": status, "planned_start": START, "confirmed": True, "forecast": None,
        **changes,
    }


def _goal(status: str = "active", target: float = 80.0) -> dict[str, Any]:
    return {"source": "profile:a:b", "target_percent": target, "maximum_percent": None, "status": status}


def _sum(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "available": True, "present": True, "control": _control(), "departure": None,
    }
    return summarize(**{**values, **changes})


def test_nothing_planned_is_direct_control() -> None:
    assert _sum() == {
        "controller": "direct", "waiting": "none",
        "next": {"event": "none", "at": None},
        "target": {"kind": "none", "value": None, "deadline": None},
        "vehicle": "connected",
    }


@pytest.mark.parametrize(("present", "vehicle", "waiting"), [
    (False, "none", "no_vehicle"), (None, "unknown", "until_planned_start"),
])
def test_vehicle_presence(present: bool | None, vehicle: str, waiting: str) -> None:
    result = _sum(present=present, departure=_plan(), control=_control(soc_goal=_goal()))
    assert (result["controller"], result["vehicle"], result["waiting"]) == ("departure_plan", vehicle, waiting)


@pytest.mark.parametrize(("status", "waiting", "event", "at"), [
    ("paused_until_start", "until_planned_start", "plan_start", START),
    ("minimum_level", "none", "plan_start", START),
    ("charging", "none", "departure", END),
    ("estimated_reached", "estimated_target_reached", "departure", END),
    ("vehicle_unconfirmed", "vehicle_unconfirmed", "departure", END),
    ("waiting_for_device", "waiting_for_device", "departure", END),
    ("waiting_for_soc", "waiting_for_soc", "departure", END),
    ("waiting_for_capacity", "waiting_for_capacity", "departure", END),
    ("waiting_for_target", "waiting_for_target", "departure", END),
    ("read_only", "read_only", "departure", END),
    ("queued", "command_pending", "departure", END),
    ("write_pending", "command_pending", "departure", END),
])
def test_departure_plan(status: str, waiting: str, event: str, at: str) -> None:
    result = _sum(departure=_plan(status), control=_control(soc_goal=_goal()))
    assert result["controller"] == "departure_plan"
    assert result["waiting"] == waiting
    assert result["next"] == {"event": event, "at": at}
    assert result["target"] == {"kind": "level", "value": 80.0, "deadline": END}
    assert result["vehicle"] == "confirmed"


def test_charging_plan_announces_its_forecast() -> None:
    finish = "2026-11-14T05:30:00+00:00"
    forecast = {"finish_at": finish, "level_at_departure": 80, "efficiency": 0.9, "full_power_kw": None}
    result = _sum(departure=_plan("late", forecast=forecast), control=_control(soc_goal=_goal()))
    assert result["next"] == {"event": "target_reached", "at": finish}
    # A forecast does not replace the start the plan still waits for.
    result = _sum(departure=_plan(forecast=forecast), control=_control(soc_goal=_goal()))
    assert result["next"] == {"event": "plan_start", "at": START}


@pytest.mark.parametrize("trip", [
    {"deadline": None, "source": None, "finished": None},
    {"deadline": END, "source": "manual", "finished": "reached"},
])
def test_no_open_trip_is_no_plan(trip: dict[str, Any]) -> None:
    result = _sum(departure=_plan("reached", trip=trip, confirmed=False))
    assert (result["controller"], result["vehicle"]) == ("direct", "connected")


@pytest.mark.parametrize(("changes", "waiting"), [
    ({"charging_release_requested": False}, "release_off"),
    ({"plug_charge": {"enabled": True, "authorized": False}}, "plug_charge_waiting"),
    ({"soc_goal": _goal("reached")}, "target_reached"),
    ({"energy_goal": {"target_kwh": 5.0, "baseline_kwh": 1.0, "status": "reached"}}, "target_reached"),
    ({"pending_action": "resume", "pause_requested": True}, "command_pending"),
    ({"pause_requested": True}, "paused"),
    ({"plug_charge": {"enabled": True, "authorized": True}}, "none"),
    ({"plug_charge": {"enabled": True, "authorized": False}, "plug_charge_manual": True}, "none"),
])
def test_direct_control_waits(changes: dict[str, Any], waiting: str) -> None:
    assert _sum(control=_control(**changes))["waiting"] == waiting


def test_direct_targets() -> None:
    assert _sum(control=_control(soc_goal=_goal()))["target"] == {"kind": "level", "value": 80.0, "deadline": None}
    energy = {"target_kwh": 7.5, "baseline_kwh": None, "status": "active"}
    assert _sum(control=_control(energy_goal=energy))["target"] == {"kind": "energy", "value": 7.5, "deadline": None}
    assert _sum(control=_control(soc_goal=_goal("disabled")))["target"]["kind"] == "none"


def _raw(**changes: Any) -> dict[str, Any]:
    return {**_sum(control=_control(guest_mode=True)), **changes}


@pytest.mark.parametrize("raw", [
    None,
    {"controller": "direct"},
    _raw(extra=1),
    _raw(controller="other"),
    _raw(controller=5),
    _raw(waiting="other"),
    _raw(vehicle="other"),
    _raw(next=None),
    _raw(next={"event": "job_end"}),
    _raw(next={"event": "other", "at": END}),
    _raw(next={"event": "job_end", "at": None}),
    _raw(next={"event": "job_end", "at": "soon"}),
    _raw(next={"event": "none", "at": END}),
    _raw(target=None),
    _raw(target={"kind": "energy"}),
    _raw(target={"kind": "other", "value": 1.0, "deadline": None}),
    _raw(target={"kind": "energy", "value": None, "deadline": None}),
    _raw(target={"kind": "energy", "value": True, "deadline": None}),
    _raw(target={"kind": "energy", "value": 1.0, "deadline": "soon"}),
    _raw(target={"kind": "none", "value": 1.0, "deadline": None}),
    _raw(target={"kind": "none", "value": None, "deadline": END}),
])
def test_invalid_summaries_are_rejected(raw: object) -> None:
    with pytest.raises(ValueError, match="invalid operation"):
        Operation.restore(raw)


@pytest.mark.parametrize("status", ["meter_unavailable", "meter_reset"])
@pytest.mark.parametrize("guest", [False, True])
def test_untrusted_meter_is_not_reported_as_reached_target(status, guest):
    result = _sum(control=_control(guest_mode=guest, energy_goal={"status": status, "target_kwh": 5}), goal_stop=True)
    assert result["waiting"] == "meter_unavailable"
