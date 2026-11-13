"""The service job model never invents a resume after losing its scope."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_job import ChargeJob, job_time

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BINDING = "a" * 64


def request(**changes):
    return {
        "id": "a" * 32, "start_at": (NOW + timedelta(hours=1)).isoformat(),
        "finish_at": (NOW + timedelta(hours=3)).isoformat(),
        "current_a": 16, "energy_kwh": 10, **changes,
    }


def job():
    return ChargeJob.create(request(), binding=BINDING, total_kwh=100, now=NOW)


def observe(value, **changes):
    value.observe(**{
        "now": NOW + timedelta(hours=1), "present": True, "available": True,
        "matches": True, "total_kwh": 100, "meter_fresh": True, **changes,
    })


def test_schedule_start_finish_and_terminal_latch():
    value = job()
    assert value.stop_requested
    observe(value, now=NOW)
    assert value.state == "scheduled"
    observe(value)
    assert value.state == "charging"
    assert not value.stop_requested
    observe(value, now=NOW + timedelta(hours=3))
    assert value.state == "finished"
    observe(value)
    assert value.state == "finished"
    value.stop()
    value.interrupt("vehicle_changed")
    assert value.state == "finished"


@pytest.mark.parametrize(("fields", "reason"), [
    ({"available": False}, "connection_lost"),
    ({"present": None}, "connection_lost"),
    ({"present": False}, "vehicle_changed"),
    ({"matches": False}, "vehicle_changed"),
    ({"independent_stop": True}, "independent_stop"),
    ({"total_kwh": 99}, "meter_reset"),
])
def test_unusable_scope_never_rearms(fields, reason):
    value = job()
    observe(value, **fields)
    assert value.state == "interrupted"
    assert value.reason == reason
    observe(value)
    assert value.stop_requested


@pytest.mark.parametrize("total", [None, -1, True, "101", float("nan"), float("inf")])
def test_missing_meter_waits_without_estimating_energy(total):
    value = job()
    observe(value, total_kwh=total)
    assert value.state == "waiting_meter"
    assert value.energy.baseline_kwh == 100
    observe(value, total_kwh=101)
    assert value.state == "charging"
    observe(value, meter_fresh=False)
    assert value.state == "waiting_meter"
    observe(value, now=NOW + timedelta(hours=3), meter_fresh=False)
    assert value.state == "finished"


def test_counter_reset_above_initial_baseline_remains_terminal():
    value = job()
    observe(value, total_kwh=105)
    observe(value, total_kwh=104)
    assert value.reason == "meter_reset"
    observe(value, total_kwh=106)
    assert value.stop_requested


def test_energy_completion_and_manual_stop_are_latched():
    value = job()
    observe(value, total_kwh=110)
    assert value.state == "reached"
    assert ChargeJob.restore(value.snapshot()).state == "reached"
    value = job()
    value.stop()
    assert value.reason == "user_stop"
    observe(value)
    assert value.state == "stopped"
    with pytest.raises(ValueError, match="interruption"):
        value.interrupt("anything")


def test_restart_never_replays_a_job_and_normal_copy_preserves_it():
    value = job()
    raw = value.snapshot()
    assert ChargeJob.restore(raw, restarted=False).snapshot() == raw
    restored = ChargeJob.restore(raw)
    assert restored.state == "interrupted"
    assert restored.reason == "service_restarted"
    assert value.snapshot() == raw


def test_clock_rollback_is_not_a_new_window():
    value = job()
    observe(value)
    observe(value, now=NOW)
    assert value.reason == "clock_changed"


@pytest.mark.parametrize("stamp", [None, 3, [], "", "x" * 65, "wrong", "2026-01-01T00:00:00"])
def test_times_need_an_explicit_timezone(stamp):
    with pytest.raises(ValueError, match="job time"):
        job_time(stamp)


def test_offsets_are_normalized():
    assert job_time("2026-01-01T01:00:00+01:00") == NOW


@pytest.mark.parametrize("current", [True, None, "16", 0, 5.99, 32.01, float("inf"), float("nan")])
def test_current_never_becomes_unlimited(current):
    with pytest.raises(ValueError, match="current"):
        ChargeJob.create(request(current_a=current), binding=BINDING, total_kwh=100, now=NOW)


@pytest.mark.parametrize("payload", [None, [], {}, True, {"extra": 1}])
def test_request_shape_is_exact(payload):
    with pytest.raises(ValueError, match="request fields"):
        ChargeJob.create(payload, binding=BINDING, total_kwh=100, now=NOW)


@pytest.mark.parametrize("identifier", [None, True, "", "g" * 32, "a" * 31])
def test_request_id_is_bounded(identifier):
    with pytest.raises(ValueError, match="job id"):
        ChargeJob.create(request(id=identifier), binding=BINDING, total_kwh=100, now=NOW)


@pytest.mark.parametrize("binding", [None, True, "", "g" * 64])
def test_binding_is_bounded(binding):
    with pytest.raises(ValueError, match="binding"):
        ChargeJob.create(request(), binding=binding, total_kwh=100, now=NOW)


@pytest.mark.parametrize(("start", "finish"), [(-61, 3600), (3600, 3600), (7200, 3600), (0, 604801)])
def test_window_is_bounded(start, finish):
    with pytest.raises(ValueError, match="job window"):
        ChargeJob.create(request(start_at=(NOW + timedelta(seconds=start)).isoformat(), finish_at=(NOW + timedelta(seconds=finish)).isoformat()), binding=BINDING, total_kwh=100, now=NOW)


def test_zero_energy_is_not_an_unbounded_order():
    with pytest.raises(ValueError, match="positive"):
        ChargeJob.create(request(energy_kwh=0), binding=BINDING, total_kwh=100, now=NOW)


@pytest.mark.parametrize(("field", "value"), [
    ("id", None), ("id", "bad"), ("binding", None), ("binding", "bad"),
    ("state", []), ("state", "bad"), ("reason", []), ("reason", "bad"),
    ("finish_at", NOW.isoformat()),
    ("finish_at", (NOW + timedelta(days=9)).isoformat()),
    ("current_a", 0), ("energy", {"target_kwh": 0, "baseline_kwh": None, "status": "disabled"}),
])
def test_corrupt_saved_job_is_rejected(field, value):
    raw = job().snapshot()
    raw[field] = value
    with pytest.raises(ValueError, match="job"):
        ChargeJob.restore(raw)


@pytest.mark.parametrize("raw", [None, [], {}, {"version": 1}])
def test_saved_shape_is_exact(raw):
    with pytest.raises(ValueError, match="saved job fields"):
        ChargeJob.restore(raw)
