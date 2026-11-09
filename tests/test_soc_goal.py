"""Percentage goals use vehicle telemetry and never infer or authorize a start."""

from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal


def configured() -> SocGoal:
    goal = SocGoal()
    goal.configure_source("sensor.car_soc|number.car_limit")
    return goal


def test_goal_uses_real_soc_and_latches_until_explicit_change():
    goal = configured()
    assert goal.set_target(80, 50, 90)
    unchanged = goal.snapshot()
    assert not goal.observe(79)
    assert goal.snapshot() == unchanged
    assert goal.observe(80)
    assert goal.stop_requested
    assert not goal.observe(40)
    restored = SocGoal.restore(goal.snapshot())
    assert restored.stop_requested
    assert restored.set_target(85, 81)
    assert restored.status == "active"


def test_initial_soc_at_or_above_target_stops_immediately():
    goal = configured()
    goal.set_target(80, 90)
    assert goal.stop_requested


def test_a_target_at_the_car_limit_follows_it_both_ways():
    goal = configured()
    goal.set_target(100, 0)
    assert goal.maximum_percent is None
    assert goal.observe(70, 80)
    assert goal.target_percent == 80
    assert goal.observe(70, 95)
    assert (goal.maximum_percent, goal.target_percent) == (95, 95)
    assert goal.observe(70, 65)
    assert goal.target_percent == 65
    # The car ends a charge at its own limit; nothing needs to pause.
    assert goal.status == "reached"
    assert not goal.stop_requested


def test_a_target_below_the_car_limit_never_rises():
    goal = configured()
    goal.set_target(70, 50, 90)
    assert goal.observe(50, 100)
    assert (goal.maximum_percent, goal.target_percent) == (100, 70)


def test_following_a_raised_limit_lifts_only_the_reached_latch():
    goal = configured()
    goal.set_target(80, 50, 80)
    assert goal.observe(80)
    assert goal.status == "reached"
    assert goal.observe(82, 95)
    assert (goal.target_percent, goal.status) == (95, "active")
    # An unknown charge level ends the latch too; the next report decides.
    assert goal.observe(95)
    assert goal.status == "reached"
    assert goal.observe(None, 100)
    assert (goal.target_percent, goal.status) == (100, "active")


def test_a_raised_limit_below_the_charge_level_keeps_the_latch():
    goal = configured()
    goal.set_target(80, 50, 80)
    assert goal.observe(85)
    assert goal.observe(90, 88)
    assert (goal.target_percent, goal.status) == (88, "reached")


@pytest.mark.parametrize("value", [None, "unavailable", True, -1, 101, float("nan"), float("inf"), 10**1000])
def test_bad_soc_keeps_existing_limit_and_does_not_pause(value):
    goal = configured()
    goal.set_target(80, 50, 90)
    before = goal.snapshot()
    assert not goal.observe(value)
    assert goal.snapshot() == before
    assert not goal.stop_requested


@pytest.mark.parametrize("value", [None, 0, False, "80", -1, 101, float("nan"), float("inf")])
def test_bad_cap_retains_last_valid_maximum(value):
    goal = configured()
    goal.set_target(80, 50, 90)
    assert not goal.observe(60, value)
    assert goal.maximum_percent == 90
    assert goal.target_percent == 80


@pytest.mark.parametrize("value", [True, "80", None, -1, 101, float("nan"), float("inf"), 10**1000])
def test_bad_target_is_rejected_without_mutation(value):
    goal = configured()
    goal.set_target(80, 50)
    before = goal.snapshot()
    with pytest.raises(ValueError, match="percentage"):
        goal.set_target(value)
    assert goal.snapshot() == before


def test_zero_needs_no_sources_and_preserves_only_a_bound_cap():
    goal = SocGoal()
    assert not goal.set_target(0, float("nan"), 0)
    assert SocGoal.restore(goal.snapshot()).snapshot() == goal.snapshot()
    assert not goal.set_target(0, None, 80)
    assert goal.maximum_percent is None
    goal.configure_source("car")
    goal.set_target(80, 50, 90)
    assert goal.set_target(0, float("nan"), 0)
    assert goal.status == "disabled"
    assert goal.maximum_percent == 90


def test_source_change_forgets_other_vehicle_and_disable_does_not_enable_a_goal():
    goal = configured()
    goal.set_target(80, 85, 90)
    assert not goal.configure_source(goal.source)
    assert goal.configure_source("sensor.another|number.another")
    assert goal.snapshot() == {
        "source": "sensor.another|number.another", "target_percent": 0.0,
        "maximum_percent": None, "status": "disabled",
    }
    assert goal.configure_source(None)
    assert not goal.observe(90, 80)
    with pytest.raises(ValueError, match="not configured"):
        goal.set_target(80)


def test_setting_above_known_maximum_is_rejected():
    goal = configured()
    goal.observe(None, 80)
    with pytest.raises(ValueError, match="exceeds"):
        goal.set_target(81)
    assert goal.status == "disabled"


@pytest.mark.parametrize("source", [1, False, "", "x" * 513])
def test_invalid_source_is_rejected(source):
    with pytest.raises(ValueError, match="source"):
        SocGoal().configure_source(source)


@pytest.mark.parametrize("changes", [
    {"source": None}, {"status": "disabled"}, {"status": []},
    {"target_percent": 0}, {"maximum_percent": 60}, {"extra": 1},
    {"maximum_percent": 0}, {"target_percent": True},
])
def test_restore_rejects_inconsistent_state(changes):
    goal = configured()
    goal.set_target(80, 50)
    with pytest.raises(ValueError, match=r"goal|percentage"):
        SocGoal.restore(goal.snapshot() | changes)


@pytest.mark.parametrize("raw", [None, [], "", {}])
def test_restore_rejects_invalid_shapes(raw):
    with pytest.raises(ValueError, match="fields"):
        SocGoal.restore(raw)


def test_only_a_target_below_the_car_limit_pauses_when_reached():
    below = configured()
    below.set_target(70, 50, 80)
    assert below.observe(70)
    assert below.stop_requested
    at_limit = configured()
    at_limit.set_target(80, 50, 80)
    assert at_limit.observe(80)
    assert at_limit.status == "reached"
    assert not at_limit.stop_requested
    # Without a known car limit, the wallbox still has to stop the charge.
    unknown = configured()
    unknown.set_target(80, 50)
    assert unknown.observe(80)
    assert unknown.stop_requested
