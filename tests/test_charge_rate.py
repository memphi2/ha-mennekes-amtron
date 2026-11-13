"""Measured AC forecasts are conditional arithmetic, never control authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_rate import (
    HISTORY_SECONDS,
    MAX_BUCKETS,
    MAX_RATES,
    ChargeRateLearner,
    RateHistory,
    _finish,
    charging_forecast,
    context,
    finite,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)
AT = NOW.timestamp()
SCOPE, PROFILE = "a" * 64, "b" * 64
KEY = (SCOPE, PROFILE, 12000, 3)


def sample(**changes):
    return SimpleNamespace(**{"now": NOW + timedelta(minutes=3), "available": True, "present": True,
                              "scope": SCOPE, "total_kwh": 100.45, "meter_fresh": True, "permitted": True,
                              "independent_stop": False, "current_a": 12, "current_cap": 16, "pending": False, **changes})


def job(**changes):
    return SimpleNamespace(**{"start_at": NOW, "finish_at": NOW + timedelta(hours=8),
                              "current_a": 12, "energy": SimpleNamespace(baseline_kwh=100, target_kwh=20),
                              "binding": SCOPE, "state": "charging", "terminal": False,
                              "reserve": None, "boost_until": None, **changes})


def train(rates=(9, 9, 9), *, key=KEY):
    learner, energy = ChargeRateLearner(), 100.0
    learner.observe(key, energy=energy, at=AT, monotonic=1000, now=AT, charging=True)
    for index, rate in enumerate(rates, 1):
        energy += rate / 60
        learner.observe(key, energy=energy, at=AT + index * 60, monotonic=1000 + index * 60,
                        now=AT + index * 60, charging=True)
    return learner, energy


def forecast(learner, value=None, data=None, **changes):
    return charging_forecast(learner, value if value is not None else job(), data if data is not None else sample(),
                             **{"profile": PROFILE, "phases": 3, "monotonic": 1180, "charging": True, "meter_at": AT + 180, **changes})


def test_live_estimate_is_a_range_and_does_not_change_inputs():
    learner, _ = train()
    value, data = job(), sample()
    before = deepcopy((vars(value), vars(data), learner.history.snapshot()))
    result = forecast(learner, value, data)
    assert result["basis"] == "ac_meter"
    assert result["state"] == "estimate"
    assert result["source"] == "live"
    assert result["samples"] == 3
    assert result["power_kw"] == 9
    assert result["remaining_kwh"] == 19.55
    earliest, latest = (datetime.fromisoformat(result[k]) for k in ("earliest_finish", "latest_finish"))
    assert data.now < earliest < latest < value.finish_at
    assert result["deadline_status"] == "likely"
    assert before == (vars(value), vars(data), learner.history.snapshot())
    assert SCOPE not in str(result)
    assert PROFILE not in str(result)


@pytest.mark.parametrize("value", [None, True, "12", -1, float("nan"), float("inf"), 10**1000])
def test_numeric_input_does_not_coerce_unknown_data(value):
    assert finite(value, 0, 100) is None


@pytest.mark.parametrize(("scope", "profile", "amps", "phases"), [
    (None, PROFILE, 12, 3), ("bad", PROFILE, 12, 3), (SCOPE, True, 12, 3),
    (SCOPE, "bad", 12, 3), (SCOPE, PROFILE, None, 3), (SCOPE, PROFILE, 5, 3),
    (SCOPE, PROFILE, 33, 3), (SCOPE, PROFILE, 12, True), (SCOPE, PROFILE, 12, 4),
])
def test_bad_context_is_not_replaced_by_a_default(scope, profile, amps, phases):
    assert context(scope, profile, amps, phases) is None


def test_context_retains_unknown_phases_and_unbound_live_sessions():
    assert context(SCOPE, None, 12.123, 0) == (SCOPE, None, 12123, 0)
    learner, _ = train(key=(SCOPE, None, 12000, 0))
    assert learner.history.snapshot() == []
    assert forecast(learner, profile=None, phases=0)["source"] == "live"
    assert learner.history.get((SCOPE, None, 12000, 0), AT) is None


@pytest.mark.parametrize("change", [
    {"energy": None}, {"energy": True}, {"at": None}, {"at": AT + 500},
    {"monotonic": float("nan")}, {"now": float("inf")}, {"now": AT + 1000},
])
def test_invalid_meter_observation_discards_live_evidence(change):
    learner, _ = train()
    values = {"energy": 101, "at": AT + 240, "monotonic": 1240, "now": AT + 240, "charging": True, **change}
    learner.observe(KEY, **values)
    assert learner.reason == "meter_unavailable"
    assert learner.rates(KEY, now=AT + 240, monotonic=1240, historical=False)[1] == "none"


@pytest.mark.parametrize(("key", "charging"), [(None, True), (KEY, False), (("c" * 64, PROFILE, 12000, 3), True), ((SCOPE, PROFILE, 6000, 1), True)])
def test_pause_connection_and_setpoint_changes_never_bridge_intervals(key, charging):
    learner, _ = train()
    learner.observe(key, energy=110, at=AT + 240, monotonic=1240, now=AT + 240, charging=charging)
    assert learner.rates(KEY, now=AT + 240, monotonic=1240, historical=False)[1] == "none"


@pytest.mark.parametrize(("energy", "stamp", "tick"), [(99, 240, 1240), (101, 180, 1240), (101, 240, 1180)])
def test_counter_reset_or_time_reversal_discards_live_samples(energy, stamp, tick):
    learner, _ = train()
    learner.observe(KEY, energy=energy, at=AT + stamp, monotonic=tick, now=AT + 240, charging=True)
    assert learner.reason == "meter_reset"


@pytest.mark.parametrize(("stamp", "tick"), [(400, 1400), (240, 1300)])
def test_gap_and_clock_jump_do_not_create_a_training_interval(stamp, tick):
    learner, _ = train()
    learner.observe(KEY, energy=101, at=AT + stamp, monotonic=tick, now=AT + stamp, charging=True)
    assert learner.reason == "measurement_gap"


def test_cached_reports_do_not_extend_freshness_and_short_intervals_accumulate():
    learner = ChargeRateLearner()
    for seconds in (0, 10, 20, 30):
        learner.observe(KEY, energy=100 + seconds / 360, at=AT + seconds, monotonic=1000 + seconds, now=AT + seconds, charging=True)
    assert learner.history.generation == 1
    learner, total = train()
    learner.observe(KEY, energy=total, at=AT + 180, monotonic=1300, now=AT + 300, charging=True)
    assert forecast(learner, data=sample(now=NOW + timedelta(seconds=400)), monotonic=1400, meter_at=AT + 400)["state"] == "stale"
    assert learner.history.generation == 3


@pytest.mark.parametrize(("rate", "reason"), [(0, "no_energy_progress"), (50, "meter_jump")])
def test_no_progress_and_impossible_jump_do_not_become_confident_rates(rate, reason):
    learner, total = train()
    learner.observe(KEY, energy=total + rate / 60, at=AT + 240, monotonic=1240, now=AT + 240, charging=True)
    assert learner.reason == reason
    assert forecast(learner, data=sample(now=NOW + timedelta(minutes=4)), monotonic=1240)["state"] == reason
    assert learner.history.generation == 3


def test_history_is_source_current_phase_scoped_bounded_and_expiring():
    learner, _ = train(tuple(6 + i / 10 for i in range(25)))
    book = learner.history
    assert len(book.records[(PROFILE, 12000, 3)].rates) == MAX_RATES
    assert len(learner.rates(KEY, now=AT + 1500, monotonic=2500, historical=False)[0]) == 8
    restored = RateHistory.restore(book.snapshot())
    assert restored.snapshot() == book.snapshot()
    assert restored.get((SCOPE, "c" * 64, 12000, 3), AT + 1500) is None
    assert restored.get((SCOPE, PROFILE, 6000, 3), AT + 1500) is None
    assert restored.get((SCOPE, PROFILE, 12000, 1), AT + 1500) is None
    assert restored.get(KEY, AT) is None
    assert restored.get(KEY, AT + HISTORY_SECONDS + 1501) is None
    restored.add(KEY, 5, AT + HISTORY_SECONDS + 1501)
    assert restored.get(KEY, AT + HISTORY_SECONDS + 1501).rates == (5,)
    restored.add(KEY, 4, AT)
    assert restored.get(KEY, AT).rates == (4,)
    for i in range(MAX_BUCKETS + 1):
        restored.add((SCOPE, f"{i:064x}", 12000, 3), 9, AT + i)
    assert len(restored.records) == MAX_BUCKETS
    assert restored.get((SCOPE, "0" * 64, 12000, 3), AT + 100) is None


@pytest.mark.parametrize("raw", [None, {}, [None], [{}], [None] * (MAX_BUCKETS + 1)])
def test_invalid_history_container_is_rejected(raw):
    with pytest.raises(ValueError, match="rate history"):
        RateHistory.restore(raw)


@pytest.mark.parametrize(("key", "value"), [
    ("source", True), ("source", "bad"), ("current_ma", True), ("current_ma", 5999),
    ("phases", True), ("phases", 0), ("rates_kw", None), ("rates_kw", []),
    ("rates_kw", [9] * (MAX_RATES + 1)), ("rates_kw", [float("nan")]),
    ("rates_kw", [0]), ("updated_at", None), ("updated_at", float("inf")),
])
def test_invalid_history_value_is_rejected(key, value):
    learner, _ = train()
    raw = learner.history.snapshot()
    raw[0][key] = value
    with pytest.raises(ValueError, match="rate history"):
        RateHistory.restore(raw)


def test_duplicate_history_and_no_job_have_explicit_outcomes():
    learner, _ = train()
    row = learner.history.snapshot()[0]
    with pytest.raises(ValueError, match="duplicate"):
        RateHistory.restore([row, row])
    assert charging_forecast(learner, None, sample(), profile=PROFILE, phases=3, monotonic=1180, charging=True)["state"] == "no_job"
    assert forecast(learner, job(terminal=True, state="reached"))["state"] == "complete"
    assert forecast(learner, job(terminal=True, state="stopped"))["state"] == "stopped"
    assert forecast(learner, data=sample(total_kwh=120))["state"] == "complete"


@pytest.mark.parametrize("change", [
    {"available": False}, {"present": False}, {"scope": "c" * 64},
    {"meter_fresh": False}, {"total_kwh": None}, {"total_kwh": 99},
])
def test_missing_current_evidence_hides_the_range(change):
    learner, _ = train()
    result = forecast(learner, data=sample(**change))
    assert result["state"] == "unavailable"
    assert result["earliest_finish"] is None


def test_missing_baseline_and_not_charging_do_not_reuse_history():
    learner, _ = train()
    assert forecast(learner, job(energy=SimpleNamespace(baseline_kwh=None, target_kwh=20)))["state"] == "unavailable"
    assert forecast(learner, charging=False)["state"] == "waiting_vehicle"
    for change in ({"pending": True}, {"independent_stop": True}, {"permitted": False}, {"current_a": None}):
        assert forecast(learner, data=sample(**change))["earliest_finish"] is None


def test_history_is_conditional_only_for_an_existing_scheduled_pause():
    learner, _ = train()
    learner.invalidate("not_charging")
    planned = job(state="scheduled", start_at=NOW + timedelta(hours=4))
    result = forecast(learner, planned, sample(current_a=1), charging=False)
    assert result["source"] == "history"
    assert datetime.fromisoformat(result["earliest_finish"]) > planned.start_at
    assert forecast(learner, planned, profile="c" * 64, charging=False)["state"] == "scheduled"
    empty = ChargeRateLearner()
    assert forecast(empty)["state"] == "learning"
    learner.history = RateHistory()
    learner.history.add(KEY, 9, AT)
    assert forecast(learner, planned, charging=False)["state"] == "scheduled"


@pytest.mark.parametrize(("rates", "state"), [((1, 1, 10), "unstable"), ((0.055, 0.055, 0.055), "unstable"), ((0.1, 0.1, 0.1), "beyond_horizon")])
def test_variability_and_horizon_prevent_false_precision(rates, state):
    learner, energy = train(rates)
    assert forecast(learner, job(energy=SimpleNamespace(baseline_kwh=100, target_kwh=1000)), sample(total_kwh=energy))["state"] == state


def test_deadline_risk_is_not_a_new_stop_or_start_policy():
    learner, _ = train()
    assert forecast(learner, job(finish_at=NOW + timedelta(hours=2.25)))["deadline_status"] == "at_risk"
    assert forecast(learner, job(finish_at=NOW + timedelta(hours=1)))["deadline_status"] == "unlikely"


def test_finish_arithmetic_includes_reserve_and_boost_without_double_counting():
    planned = job(start_at=NOW + timedelta(hours=4))
    assert _finish(planned, 20, 0, NOW, 10) == NOW + timedelta(hours=6)
    planned.reserve = SimpleNamespace(target_kwh=5, reached=False)
    assert _finish(planned, 20, 0, NOW, 10) == NOW + timedelta(hours=5.5)
    planned.boost_until = NOW + timedelta(hours=1)
    assert _finish(planned, 20, 0, NOW, 10) == NOW + timedelta(hours=5)
    planned.reserve.target_kwh = 20
    assert _finish(planned, 20, 0, NOW, 10) == NOW + timedelta(hours=2)
    planned.reserve.reached = True
    planned.boost_until = NOW
    assert _finish(planned, 15, 5, NOW, 10) == NOW + timedelta(hours=5.5)
    assert _finish(planned, 15, 5, NOW + timedelta(hours=5), 10) == NOW + timedelta(hours=6.5)


def test_current_and_phase_plausibility_reject_impossible_intervals_and_history():
    key = (SCOPE, PROFILE, 6000, 1)
    learner, _ = train((3, 3, 3), key=key)
    assert learner.reason == "meter_jump"
    assert learner.history.snapshot() == []
    raw = [{"source": PROFILE, "current_ma": 6000, "phases": 1, "rates_kw": [3], "updated_at": AT}]
    with pytest.raises(ValueError, match="rate history value"):
        RateHistory.restore(raw)


def test_cached_reads_do_not_slide_the_estimated_finish_forward():
    learner, _ = train()
    initial = forecast(learner)
    later = forecast(learner, data=sample(now=NOW + timedelta(minutes=4)), monotonic=1240)
    assert initial["earliest_finish"] == later["earliest_finish"]
    assert initial["latest_finish"] == later["latest_finish"]
    nearly_done = sample(now=NOW + timedelta(minutes=5))
    result = forecast(learner, job(energy=SimpleNamespace(baseline_kwh=100, target_kwh=0.5)), data=nearly_done, monotonic=1300)
    assert result["state"] == "awaiting_meter"
    assert result["latest_finish"] is None
    assert result["remaining_kwh"] == 0.05


@pytest.mark.parametrize("meter_at", [None, True, AT - 500, AT + 500])
def test_a_current_frame_requires_its_own_valid_meter_timestamp(meter_at):
    learner, _ = train()
    assert forecast(learner, meter_at=meter_at)["state"] == "unavailable"


def test_current_meter_and_learning_frame_cannot_be_mixed():
    learner, _ = train()
    for values in ({"meter_at": AT + 179}, {"data": sample(total_kwh=101)}):
        result = forecast(learner, **values)
        assert result["state"] == "awaiting_meter"
        assert result["earliest_finish"] is None
