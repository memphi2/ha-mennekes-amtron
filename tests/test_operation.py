"""The service's summary of who controls the charge, why it waits and what comes next."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from custom_components.mennekes_amtron_rtu.device.operation import (
    CONTROLLERS,
    EVENTS,
    WAITING,
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


def _job(state: str = "scheduled", **changes: Any) -> dict[str, Any]:
    return {
        "id": "job-1", "start_at": START, "finish_at": END, "current_a": 16.0,
        "energy_kwh": 12.0, "state": state, "reason": state, "holding": True, **changes,
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
        "job": None, "weekly": None,
    }
    return summarize(**{**values, **changes})


def test_nothing_planned_is_direct_control() -> None:
    assert _sum() == {
        "controller": "direct", "waiting": "none",
        "next": {"event": "none", "at": None},
        "target": {"kind": "none", "value": None, "deadline": None},
        "vehicle": "connected",
    }


def test_unreachable_wallbox_overrides_every_controller() -> None:
    result = _sum(available=False, job=_job(), departure=_plan())
    assert (result["controller"], result["waiting"], result["vehicle"]) == (
        "unavailable", "wallbox_unavailable", "unknown",
    )


@pytest.mark.parametrize(("present", "vehicle", "waiting"), [
    (False, "none", "no_vehicle"), (None, "unknown", "until_planned_start"),
])
def test_vehicle_presence(present: bool | None, vehicle: str, waiting: str) -> None:
    result = _sum(present=present, departure=_plan(), control=_control(soc_goal=_goal()))
    assert (result["controller"], result["vehicle"], result["waiting"]) == ("departure_plan", vehicle, waiting)


@pytest.mark.parametrize(("state", "waiting", "event", "at"), [
    ("scheduled", "until_job_start", "job_start", START),
    ("charging", "none", "job_end", END),
    ("waiting_meter", "meter_unavailable", "none", None),
    ("suspended", "job_suspended", "none", None),
    ("reached", "job_ended", "none", None),
    ("interrupted", "job_ended", "none", None),
])
def test_a_holding_order_controls(state: str, waiting: str, event: str, at: str | None) -> None:
    # The order comes before a departure plan, which reports order_active.
    result = _sum(job=_job(state), departure=_plan("order_active"))
    assert result["controller"] == "charging_job"
    assert result["waiting"] == waiting
    assert result["next"] == {"event": event, "at": at}
    assert result["target"] == {"kind": "energy", "value": 12.0, "deadline": END}


def test_order_kinds() -> None:
    assert _sum(job=_job(kind="guest"))["controller"] == "guest_order"
    weekly = {"plan_id": "p", "job_id": "job-1", "start_at": START}
    assert _sum(job=_job(), weekly=weekly)["controller"] == "weekly_plan"
    assert _sum(job=_job(), weekly={**weekly, "job_id": "other"})["controller"] == "charging_job"


def test_an_order_that_let_go_no_longer_controls() -> None:
    result = _sum(job=_job("reached", holding=False))
    assert (result["controller"], result["waiting"]) == ("direct", "none")


def test_weekly_plan_between_departures_still_controls() -> None:
    # The plan's ended order no longer holds the wallbox, but its id stays.
    done = _job("reached", holding=False)
    result = _sum(job=done, weekly={"plan_id": "p", "job_id": "job-1", "start_at": START})
    assert (result["controller"], result["waiting"]) == ("weekly_plan", "until_weekly_window")
    assert result["next"] == {"event": "weekly_window", "at": START}


def test_weekly_plan_waits_for_its_window() -> None:
    result = _sum(weekly={"plan_id": "p", "job_id": None, "start_at": START})
    assert result["controller"] == "weekly_plan"
    assert result["waiting"] == "until_weekly_window"
    assert result["next"] == {"event": "weekly_window", "at": START}
    # Without a coming window there is no event to announce.
    result = _sum(weekly={"plan_id": "p", "job_id": None, "start_at": None})
    assert result["next"] == {"event": "none", "at": None}


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


def test_blockers_come_before_the_controllers_reason() -> None:
    control = _control(charging_release_requested=False)
    assert _sum(job=_job(), control=control)["waiting"] == "release_off"
    assert _sum(goal_stop=True)["waiting"] == "target_reached"
    # A charging plan still reports a manual pause.
    assert _sum(departure=_plan("charging"), control=_control(pause_requested=True))["waiting"] == "paused"


def test_direct_targets() -> None:
    assert _sum(control=_control(soc_goal=_goal()))["target"] == {"kind": "level", "value": 80.0, "deadline": None}
    energy = {"target_kwh": 7.5, "baseline_kwh": None, "status": "active"}
    assert _sum(control=_control(energy_goal=energy))["target"] == {"kind": "energy", "value": 7.5, "deadline": None}
    assert _sum(control=_control(soc_goal=_goal("disabled")))["target"]["kind"] == "none"


def test_a_queued_command_is_next_when_it_comes_first() -> None:
    early, late = "2026-11-14T01:00:00+00:00", "2026-11-14T03:00:00+00:00"
    queued = _control(pending_action="resume", pending_until=early)
    assert _sum(control=queued)["next"] == {"event": "queued_command", "at": early}
    assert _sum(control=queued, job=_job())["next"] == {"event": "queued_command", "at": early}
    later = _control(pending_action="resume", pending_until=late)
    assert _sum(control=later, job=_job())["next"] == {"event": "job_start", "at": START}
    # A time without a queued command, or an unreadable one, announces nothing.
    assert _sum(control=_control(pending_until=early))["next"]["event"] == "none"
    for bad in ("soon", "2026-11-14T01:00:00", 5, "x" * 70):
        assert _sum(control=_control(pending_action="resume", pending_until=bad))["next"]["event"] == "none"


def test_malformed_sections_are_ignored() -> None:
    result = _sum(control={"soc_goal": 5, "energy_goal": None, "plug_charge": "x"}, departure=_plan(status=5))
    assert (result["controller"], result["waiting"]) == ("departure_plan", "none")
    assert result["target"]["kind"] == "none"
    assert _sum(job=_job(energy_kwh=True))["target"]["kind"] == "none"
    # A job state this summary does not know names no reason of its own.
    assert _sum(job=_job("odd"))["waiting"] == "none"


def test_every_summary_restores() -> None:
    forecast = {"finish_at": END, "level_at_departure": 80, "efficiency": 0.9, "full_power_kw": None}
    for raw in (
        _sum(), _sum(available=False), _sum(job=_job("charging")),
        _sum(departure=_plan("charging", forecast=forecast), control=_control(soc_goal=_goal())),
    ):
        restored = Operation.restore(raw)
        assert restored.controller in CONTROLLERS
        assert restored.waiting in WAITING
        assert restored.next_event in EVENTS
    restored = Operation.restore(_sum(job=_job("charging")))
    assert restored.next_at == datetime(2026, 11, 14, 6, tzinfo=UTC)
    assert (restored.target_kind, restored.target_value, restored.target_deadline) == (
        "energy", 12.0, datetime(2026, 11, 14, 6, tzinfo=UTC),
    )


def _raw(**changes: Any) -> dict[str, Any]:
    return {**_sum(job=_job("charging")), **changes}


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
