"""An energy goal survives reloads without guessing through meter failures."""

from __future__ import annotations

import copy

import pytest

from custom_components.mennekes_amtron_rtu.device.energy_goal import (
    ENERGY_GOAL_STATES,
    MAX_ENERGY_GOAL_KWH,
    MIN_ENERGY_GOAL_KWH,
    EnergyGoal,
)


def test_disabled_goal_needs_no_meter_and_has_no_stop_request():
    goal = EnergyGoal()
    assert goal.target_kwh == 0
    assert goal.baseline_kwh is None
    assert goal.status == "disabled"
    assert not goal.stop_requested
    assert not goal.observe(None, fresh=False)
    assert not goal.set_target(0, "unreadable")
    assert EnergyGoal.restore(goal.snapshot()).snapshot() == goal.snapshot()


def test_goal_is_additional_energy_and_progress_does_not_change_durable_state():
    goal = EnergyGoal()
    assert goal.set_target(12, 1234.5)
    initial = goal.snapshot()
    assert initial == {
        "target_kwh": 12.0, "baseline_kwh": 1234.5, "status": "active",
    }
    assert not goal.stop_requested
    assert not goal.observe(1234.5)
    assert not goal.observe(1235)
    assert not goal.observe(1246.49)
    assert goal.snapshot() == initial
    assert goal.observe(1246.5)
    assert goal.status == "reached"
    assert goal.stop_requested
    assert not goal.observe(None, fresh=False)
    assert not goal.observe(0)
    assert not goal.observe(1247)
    assert goal.status == "reached"


@pytest.mark.parametrize("target", [MIN_ENERGY_GOAL_KWH, MAX_ENERGY_GOAL_KWH, 0.1 * 3])
def test_boundary_targets_and_binary_float_steps_are_accepted(target):
    goal = EnergyGoal()
    assert goal.set_target(target, 100)
    assert goal.target_kwh == round(target, 1)
    restored = EnergyGoal.restore(goal.snapshot())
    assert restored.target_kwh == goal.target_kwh
    assert restored.observe(100 + goal.target_kwh)
    assert restored.status == "reached"


@pytest.mark.parametrize(
    "target",
    [True, False, None, "12", [], {}, -1, 0.09, 0.15, 1000.1, float("nan"), float("inf"), 10**400],
)
def test_invalid_targets_do_not_replace_an_active_goal(target):
    goal = EnergyGoal()
    goal.set_target(10, 100)
    original = goal.snapshot()
    with pytest.raises(ValueError, match="energy"):
        goal.set_target(target, 105)
    assert goal.snapshot() == original


@pytest.mark.parametrize(
    "reading",
    [None, True, False, "100", -1, [], {}, float("nan"), float("inf"), 10**400, 1e308],
)
def test_arming_requires_a_valid_representable_meter_without_losing_existing_goal(reading):
    goal = EnergyGoal()
    goal.set_target(10, 100)
    original = goal.snapshot()
    with pytest.raises(ValueError, match="energy"):
        goal.set_target(12, reading)
    assert goal.snapshot() == original


def test_every_explicit_target_sets_a_new_anchor_and_clears_old_stop_latch():
    goal = EnergyGoal()
    goal.set_target(12, 100)
    assert not goal.set_target(12, 100)
    assert not goal.observe(103)
    assert goal.set_target(12, 103)
    assert goal.baseline_kwh == 103
    assert not goal.observe(112)
    assert not goal.stop_requested
    assert goal.observe(115)
    assert goal.set_target(12, 115)
    assert goal.status == "active"
    assert not goal.stop_requested


@pytest.mark.parametrize("state", ["active", "reached", "meter_unavailable", "meter_reset"])
def test_restoring_preserves_goal_and_stop_latch(state):
    stored = {"target_kwh": 12.0, "baseline_kwh": 100.0, "status": state}
    goal = EnergyGoal.restore(stored)
    stored["target_kwh"] = 20
    assert goal.target_kwh == 12
    assert goal.baseline_kwh == 100
    assert goal.status == state
    assert goal.stop_requested is (state != "active")
    if state == "active":
        assert not goal.observe(111.9)
        assert goal.observe(112)
    elif state == "meter_unavailable":
        assert goal.observe(120)
        assert goal.status == "reached"
    else:
        assert not goal.observe(120)
        assert goal.status == state


@pytest.mark.parametrize("reading", [None, True, "12", -1, float("nan"), float("inf"), 10**400])
def test_invalid_measurements_latch_stop_without_claiming_completed(reading):
    goal = EnergyGoal()
    goal.set_target(12, 100)
    assert goal.observe(reading)
    assert goal.status == "meter_unavailable"
    assert goal.stop_requested
    assert goal.observe(112)
    assert goal.status == "reached"


def test_stale_measurement_cannot_complete_or_rearm_goal():
    goal = EnergyGoal()
    goal.set_target(12, 100)
    assert goal.observe(112, fresh=False)
    assert goal.status == "meter_unavailable"
    assert goal.stop_requested
    assert goal.observe(112)
    assert goal.status == "reached"


@pytest.mark.parametrize("reading", [0, 99.9, 100, 105.9])
def test_counter_fall_is_detected_even_when_above_the_baseline(reading):
    goal = EnergyGoal()
    goal.set_target(12, 100)
    goal.observe(106)
    assert goal.observe(reading)
    assert goal.status == "meter_reset"
    assert goal.stop_requested
    assert not goal.observe(112)
    assert goal.status == "meter_reset"


def test_restored_goal_detects_counter_reset_below_its_durable_baseline():
    goal = EnergyGoal()
    goal.set_target(12, 100)
    restored = EnergyGoal.restore(goal.snapshot())
    assert restored.observe(0)
    assert restored.status == "meter_reset"
    assert restored.stop_requested


@pytest.mark.parametrize("state", ENERGY_GOAL_STATES)
def test_disable_clears_every_goal_state_without_a_working_meter(state):
    goal = EnergyGoal() if state == "disabled" else EnergyGoal.restore(
        {"target_kwh": 12, "baseline_kwh": 100, "status": state}
    )
    assert goal.set_target(0, None) is (state != "disabled")
    assert goal.status == "disabled"
    assert goal.target_kwh == 0
    assert goal.baseline_kwh is None
    assert not goal.stop_requested


@pytest.mark.parametrize(
    "stored",
    [
        None, [], {},
        {"target_kwh": 12, "baseline_kwh": 100},
        {"target_kwh": 12, "baseline_kwh": 100, "status": "active", "extra": 1},
        {"target_kwh": True, "baseline_kwh": 100, "status": "active"},
        {"target_kwh": float("nan"), "baseline_kwh": 100, "status": "active"},
        {"target_kwh": 0.15, "baseline_kwh": 100, "status": "active"},
        {"target_kwh": 12, "baseline_kwh": None, "status": "active"},
        {"target_kwh": 12, "baseline_kwh": False, "status": "active"},
        {"target_kwh": 12, "baseline_kwh": float("inf"), "status": "active"},
        {"target_kwh": 12, "baseline_kwh": 1e308, "status": "active"},
        {"target_kwh": 12, "baseline_kwh": 100, "status": "disabled"},
        {"target_kwh": 12, "baseline_kwh": 100, "status": "invented"},
        {"target_kwh": 12, "baseline_kwh": 100, "status": []},
        {"target_kwh": 0, "baseline_kwh": 100, "status": "disabled"},
        {"target_kwh": 0, "baseline_kwh": None, "status": "active"},
    ],
)
def test_corrupt_saved_goal_is_rejected_instead_of_silently_disabling(stored):
    original = copy.deepcopy(stored)
    with pytest.raises(ValueError, match="energy"):
        EnergyGoal.restore(stored)
    # Restore does not repair/mutate the persisted source behind its owner's back.
    assert repr(stored) == repr(original)


@pytest.mark.parametrize(("reading", "expected"), [(101, "active"), (112, "reached"), (99, "meter_reset")])
def test_meter_outage_recovers_only_from_a_fresh_valid_counter(reading, expected):
    goal = EnergyGoal()
    goal.set_target(12, 100)
    goal.observe(None)
    goal.resume_on_recovery = True
    assert not goal.observe(None)
    assert goal.observe(reading)
    assert goal.status == expected
    assert goal.resume_on_recovery is (expected == "active")
    assert EnergyGoal.restore(goal.snapshot()).snapshot() == goal.snapshot()
    assert not EnergyGoal.restore(goal.snapshot()).resume_on_recovery


@pytest.mark.parametrize(("status", "grant"), [("active", 1), ("disabled", True), ("reached", True), ("meter_reset", True)])
def test_corrupt_recovery_grants_are_rejected(status, grant):
    with pytest.raises(ValueError, match="energy"):

        EnergyGoal.restore({
            "target_kwh": 0 if status == "disabled" else 12,
            "baseline_kwh": None if status == "disabled" else 100,
            "status": status, "resume_on_recovery": grant,
        })
