"""Calendar proposals cannot silently become charging permission."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

import pytest

from custom_components.mennekes_amtron_rtu.device.weekly_plan import WeeklyPlan

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def payload(**changes):
    return {
        "id": "a" * 32,
        "vehicle_profile": "bmw-i4",
        "timezone": "Europe/Berlin",
        "weekdays": [0, 3],
        "departure_time": "07:40",
        "charge_minutes": 240,
        "current_a": 16,
        "energy_kwh": 20.0,
        **changes,
    }


def plan(**changes):
    return WeeklyPlan.parse(payload(**changes))


def test_round_trip_is_canonical_and_does_not_mutate_input():
    raw = payload(weekdays=[3, 0], exceptions=[
        {"date": "2026-11-19", "departure_time": "08:15"},
        {"date": "2026-11-16", "departure_time": None},
    ])
    before = deepcopy(raw)
    parsed = WeeklyPlan.parse(raw)
    assert raw == before
    snapshot = parsed.snapshot()
    assert snapshot["weekdays"] == [0, 3]
    assert snapshot["exceptions"][0]["date"] == "2026-11-16"
    assert WeeklyPlan.parse(json.loads(json.dumps(snapshot))).snapshot() == snapshot
    snapshot["weekdays"].clear()
    assert parsed.weekdays == (0, 3)


@pytest.mark.parametrize("raw", [None, [], {}, True, "plan"])
def test_bad_payload(raw):
    with pytest.raises(ValueError, match="weekly plan fields"):
        WeeklyPlan.parse(raw)


@pytest.mark.parametrize("key", list(payload()))
def test_every_required_field_is_required(key):
    raw = payload()
    del raw[key]
    with pytest.raises(ValueError, match="weekly plan fields"):
        WeeklyPlan.parse(raw)


def test_unknown_fields_are_not_silently_ignored():
    with pytest.raises(ValueError, match="weekly plan fields"):
        WeeklyPlan.parse(payload(token="must-not-be-accepted"))


@pytest.mark.parametrize("value", [None, 1, True, "", "A" * 32, "a" * 31, "b" * 33, "g" * 32])
def test_plan_id(value):
    with pytest.raises(ValueError, match="weekly plan id"):
        plan(id=value)


@pytest.mark.parametrize("value", [None, True, 1, "", "x" * 65, " car", "car/name", "x\n"])
def test_profile_id(value):
    with pytest.raises(ValueError, match="weekly vehicle profile"):
        plan(vehicle_profile=value)


@pytest.mark.parametrize("value", [None, True, [], "", " ", "/etc/passwd", "../UTC", "Europe/../UTC", "Europe//Berlin", "not-a-timezone", "x" * 129, "Europe/Berlin\n"])
def test_timezone(value):
    with pytest.raises(ValueError, match="weekly timezone"):
        plan(timezone=value)


@pytest.mark.parametrize("value", [None, True, "0", [], [True], [-1], [7], [0, 0], [0.0], [[0]], list(range(8))])
def test_weekdays(value):
    with pytest.raises(ValueError, match="weekly weekdays"):
        plan(weekdays=value)


@pytest.mark.parametrize("value", [None, 7, True, "7:40", "24:00", "07:60", "07:40:00", "07:40Z", " 07:40", "07:40\n", "０７:４０"])
def test_departure_format(value):
    with pytest.raises(ValueError, match="weekly departure"):
        plan(departure_time=value)


@pytest.mark.parametrize("value", [None, True, 0, -1, 1441, 30.0, "30", float("nan"), float("inf")])
def test_duration(value):
    with pytest.raises(ValueError, match="weekly charge_minutes"):
        plan(charge_minutes=value)


@pytest.mark.parametrize("value", [None, True, "16", 0, 5.99, 32.01, float("inf"), float("nan"), 10**1000])
def test_current(value):
    with pytest.raises(ValueError, match="weekly current"):
        plan(current_a=value)


@pytest.mark.parametrize("value", [None, True, "20", 0, -1, 0.09, 0.15, 1000.1, float("inf"), float("nan"), 10**1000])
def test_energy_uses_existing_goal_validation(value):
    with pytest.raises(ValueError, match=r"energy|finite"):
        plan(energy_kwh=value)


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_enabled_is_strict_boolean(value):
    with pytest.raises(ValueError, match="weekly enabled"):
        plan(enabled=value)


@pytest.mark.parametrize("value", [None, 0, 1, True, "", "first", [], {}])
def test_fold_policy(value):
    with pytest.raises(ValueError, match="weekly ambiguous_time"):
        plan(ambiguous_time=value)


@pytest.mark.parametrize("value", [None, {}, True, [None], [{}], [{"date": "2026-11-16"}], [{"date": "2026-11-16", "departure_time": None, "extra": 1}]])
def test_exception_format(value):
    with pytest.raises(ValueError, match=r"weekly exceptions|weekly exception fields"):
        plan(exceptions=value)


@pytest.mark.parametrize("value", [None, True, 1, "2026-11-31", "2026-02-29", "20261116", "2026-W47-1", "2026-1-01"])
def test_exception_date(value):
    with pytest.raises(ValueError, match="weekly exception date"):
        plan(exceptions=[{"date": value, "departure_time": None}])


def test_duplicate_and_unbounded_exceptions():
    item = {"date": "2026-11-16", "departure_time": None}
    with pytest.raises(ValueError, match="duplicate weekly exception"):
        plan(exceptions=[item, item])
    with pytest.raises(ValueError, match="too many or invalid weekly exceptions"):
        plan(exceptions=[item] * 33)
    with pytest.raises(ValueError, match="weekly departure"):
        plan(exceptions=[{**item, "departure_time": "25:00"}])


def test_disabled_has_no_occurrences():
    assert plan(enabled=False).upcoming(now=NOW) == ()


def test_normal_weekly_recurrence_and_local_clock():
    rows = plan().upcoming(now=NOW)
    assert [r.local_date.isoformat() for r in rows] == [
        "2026-11-16", "2026-11-19", "2026-11-23", "2026-11-26",
    ]
    assert rows[0].finish_at == datetime(2026, 11, 16, 6, 40, tzinfo=UTC)
    assert rows[0].start_at == datetime(2026, 11, 16, 2, 40, tzinfo=UTC)
    assert rows[0].reason is None
    assert len({r.identifier for r in rows}) == len(rows)


def test_skip_replacement_and_additional_one_off():
    rows = plan(exceptions=[
        {"date": "2026-11-16", "departure_time": None},
        {"date": "2026-11-19", "departure_time": "08:15"},
        {"date": "2026-11-17", "departure_time": "09:00"},
    ]).upcoming(now=NOW, horizon_days=7)
    assert [r.local_date.day for r in rows] == [16, 17, 19]
    assert rows[0].status == "skipped"
    assert rows[0].reason == "date_skipped"
    assert rows[0].finish_at is None
    assert rows[1].finish_at.hour == 8
    assert rows[2].finish_at.minute == 15
    with pytest.raises(ValueError, match="no valid charging window"):
        rows[0].job_request(now=NOW, vehicle_profile="bmw-i4")


@pytest.mark.parametrize(("policy", "hour"), [("earlier", 0), ("later", 1)])
def test_berlin_fall_fold_is_once_and_explicit(policy, hour):
    now = datetime(2026, 10, 24, 12, tzinfo=UTC)
    rows = plan(weekdays=[6], departure_time="02:30", ambiguous_time=policy).upcoming(now=now, horizon_days=1)
    assert len(rows) == 1
    row = rows[0]
    assert row.finish_at == datetime(2026, 10, 25, hour, 30, tzinfo=UTC)
    assert row.reason == f"ambiguous_time_{policy}"
    assert row.finish_at - row.start_at == timedelta(hours=4)


def test_first_fold_passing_does_not_silently_switch_to_second():
    now = datetime(2026, 10, 25, 0, 45, tzinfo=UTC)
    assert plan(weekdays=[6], departure_time="02:30").upcoming(now=now, horizon_days=0) == ()
    rows = plan(weekdays=[6], departure_time="02:30", ambiguous_time="later").upcoming(now=now, horizon_days=0)
    assert len(rows) == 1
    assert rows[0].finish_at == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)


def test_spring_gap_is_visible_not_shifted_into_a_charge():
    rows = plan(weekdays=[6], departure_time="02:30").upcoming(
        now=datetime(2026, 3, 28, 12, tzinfo=UTC), horizon_days=1,
    )
    assert len(rows) == 1
    assert rows[0].status == "invalid_time"
    assert rows[0].reason == "local_time_does_not_exist"
    assert rows[0].finish_at is None
    with pytest.raises(ValueError, match="no valid charging window"):
        rows[0].job_request(now=NOW, vehicle_profile="bmw-i4")


def test_spring_window_uses_elapsed_time_not_local_clock_subtraction():
    row = plan(weekdays=[6], departure_time="07:40").upcoming(
        now=datetime(2026, 3, 28, 12, tzinfo=UTC), horizon_days=1,
    )[0]
    assert row.finish_at - row.start_at == timedelta(hours=4)
    assert row.start_at.astimezone(ZoneInfo("Europe/Berlin")).hour == 3


def test_lord_howe_half_hour_fold():
    now = datetime(2026, 4, 4, 0, tzinfo=UTC)
    common = {"timezone": "Australia/Lord_Howe", "weekdays": [6], "departure_time": "01:45"}
    early = plan(**common).upcoming(now=now, horizon_days=2)[0]
    late = plan(**common, ambiguous_time="later").upcoming(now=now, horizon_days=2)[0]
    assert late.finish_at - early.finish_at == timedelta(minutes=30)
    assert late.identifier == early.identifier


def test_a_whole_deleted_local_date_is_not_fabricated():
    rows = plan(timezone="Pacific/Apia", weekdays=[4], departure_time="07:40").upcoming(
        now=datetime(2011, 12, 29, 12, tzinfo=UTC), horizon_days=2,
    )
    assert len(rows) == 1
    assert rows[0].local_date == date(2011, 12, 30)
    assert rows[0].status == "invalid_time"


def test_utc_has_no_duplicate_fold():
    rows = plan(timezone="UTC", weekdays=list(range(7)), ambiguous_time="later").upcoming(now=NOW, horizon_days=2)
    assert len(rows) == 2
    assert all(r.reason is None for r in rows)


def test_stable_ids_survive_recipe_edits_and_not_profile_change():
    first = plan().upcoming(now=NOW)[0]
    edited = plan(current_a=12, energy_kwh=10, departure_time="08:00").upcoming(now=NOW)[0]
    different = plan(vehicle_profile="other-car").upcoming(now=NOW)[0]
    assert first.identifier == edited.identifier
    assert first.identifier != different.identifier
    assert len(first.identifier) == 32
    assert int(first.identifier, 16) >= 0


def test_job_projection_preserves_absolute_proposal_and_finish():
    row = plan().upcoming(now=NOW)[0]
    proposal = row.job_request(now=NOW, vehicle_profile="bmw-i4")
    assert proposal == {
        "id": row.identifier,
        "start_at": "2026-11-16T02:40:00+00:00",
        "finish_at": "2026-11-16T06:40:00+00:00",
        "current_a": 16.0,
        "energy_kwh": 20.0,
    }
    late = row.job_request(now=row.start_at + timedelta(hours=1), vehicle_profile="bmw-i4")
    assert late["start_at"] == "2026-11-16T03:40:00+00:00"
    assert late["finish_at"] == proposal["finish_at"]
    assert late["id"] == proposal["id"]
    assert proposal["start_at"] == "2026-11-16T02:40:00+00:00"


def test_projection_rejects_wrong_profile_expired_and_too_far():
    rows = plan().upcoming(now=NOW)
    with pytest.raises(ValueError, match="profile does not match"):
        rows[0].job_request(now=NOW, vehicle_profile="guest")
    with pytest.raises(ValueError, match="next seven days"):
        rows[0].job_request(now=rows[0].finish_at, vehicle_profile="bmw-i4")
    with pytest.raises(ValueError, match="next seven days"):
        rows[-1].job_request(now=NOW, vehicle_profile="bmw-i4")


def test_snapshots_are_json_finite_and_have_no_implicit_authorization():
    for row in plan().upcoming(now=NOW):
        raw = row.snapshot()
        assert json.loads(json.dumps(raw, allow_nan=False)) == raw
        assert {"release", "unlock", "binding", "token"}.isdisjoint(raw)


class NaiveZone(tzinfo):
    def utcoffset(self, _dt):
        return None


@pytest.mark.parametrize("now", [None, "2026-11-13", 0, True, datetime(2026, 11, 13, tzinfo=UTC).replace(tzinfo=None), datetime(2026, 11, 13, tzinfo=NaiveZone())])
def test_naive_or_invalid_now_is_never_assumed_local(now):
    with pytest.raises(ValueError, match="aware datetime"):
        plan().upcoming(now=now)


@pytest.mark.parametrize(("field", "value"), [
    ("horizon_days", -1), ("horizon_days", 32), ("horizon_days", True),
    ("horizon_days", 1.0), ("limit", 0), ("limit", 33), ("limit", True),
])
def test_output_bounds(field, value):
    with pytest.raises(ValueError, match=field):
        plan().upcoming(now=NOW, **{field: value})


def test_limit_and_near_calendar_end_are_bounded():
    assert len(plan().upcoming(now=NOW, limit=1)) == 1
    rows = plan(timezone="UTC", weekdays=list(range(7))).upcoming(
        now=datetime(9999, 12, 31, 0, tzinfo=UTC), horizon_days=31,
    )
    assert len(rows) == 1
    assert rows[0].local_date == date(9999, 12, 31)


def test_leap_day_exception_and_overnight_start():
    row = plan(exceptions=[{"date": "2028-02-29", "departure_time": "01:00"}]).upcoming(
        now=datetime(2028, 2, 28, 12, tzinfo=UTC), horizon_days=1,
    )[0]
    assert row.local_date == date(2028, 2, 29)
    assert row.start_at == datetime(2028, 2, 28, 20, tzinfo=UTC)


def test_same_instant_in_different_timezones_has_identical_preview():
    a = plan().upcoming(now=NOW)
    b = plan().upcoming(now=NOW.astimezone(ZoneInfo("America/New_York")))
    assert a == b


def test_window_before_calendar_start_is_not_executable():
    rows = plan(timezone="UTC", weekdays=[0], departure_time="00:01").upcoming(
        now=datetime(1, 1, 1, 0, tzinfo=UTC), horizon_days=0,
    )
    assert rows[0].status == "invalid_time"
    assert rows[0].reason == "window_out_of_range"


def test_conversion_outside_calendar_range_is_an_explicit_failure():
    with pytest.raises(ValueError, match="local time is outside"):
        plan(timezone="Pacific/Kiritimati").upcoming(
            now=datetime(9999, 12, 31, 23, tzinfo=UTC),
        )


def test_unrepresentable_utc_departure_is_not_executable():
    rows = plan(timezone="Etc/GMT+12", weekdays=list(range(7)), departure_time="23:00").upcoming(
        now=datetime(9999, 12, 31, 15, tzinfo=UTC), horizon_days=0,
    )
    assert rows[0].status == "invalid_time"
    assert rows[0].finish_at is None
