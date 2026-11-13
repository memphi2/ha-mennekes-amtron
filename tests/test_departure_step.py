"""The plan step HA and the service share: start, pause and write decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.charging_plan import ChargePlan
from custom_components.mennekes_amtron_rtu.device.departure_step import (
    PAUSE_MARGIN,
    keeps_start,
    needs_current,
    plan_step,
    same_command,
)

NOW = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)


def test_a_distant_start_pauses_until_it():
    start = NOW + PAUSE_MARGIN + timedelta(seconds=1)
    step = plan_step(ChargePlan(16, "charging", start), None, NOW)
    assert (step.status, step.current_a, step.planned_start, step.pauses) == (
        "paused_until_start", None, start, True,
    )


def test_a_close_start_charges_now_and_remembers_when():
    step = plan_step(ChargePlan(16, "charging", NOW + PAUSE_MARGIN), None, NOW)
    assert (step.status, step.current_a, step.planned_start, step.pauses) == ("charging", 16, NOW, False)


def test_a_started_charge_keeps_going_when_the_computed_start_moves_later():
    began = NOW - timedelta(minutes=20)
    step = plan_step(ChargePlan(6, "finishing", NOW + timedelta(hours=2)), began, NOW)
    assert (step.status, step.current_a, step.planned_start) == ("finishing", 6, began)


def test_a_future_planned_start_is_planned_again():
    later = NOW + timedelta(hours=1)
    step = plan_step(ChargePlan(16, "unreachable"), later, NOW)
    assert (step.status, step.planned_start) == ("unreachable", NOW)


@pytest.mark.parametrize(("previous", "current", "same"), [
    (None, None, True), (None, 6.0, False), (6.0, None, False),
    (6.0, 6.9, True), (6.0, 7.0, False), (16.0, 15.2, True),
])
def test_a_queued_command_fits_within_one_amp(previous, current, same):
    assert same_command(previous, current) is same


@pytest.mark.parametrize(("paused", "applied", "write"), [
    (True, 10.0, True), (False, None, True), (False, "10", True), (False, 0, True),
    (False, 10.5, False), (False, 9.0, True), (False, 11.0, True),
])
def test_only_a_paused_unknown_or_distant_current_is_written(paused, applied, write):
    assert needs_current(10.0, paused=paused, applied=applied) is write


@pytest.mark.parametrize(("status", "start", "keep", "kept"), [
    ("charging", NOW + timedelta(hours=1), False, True),
    ("waiting_for_soc", NOW - timedelta(minutes=1), True, True),
    ("waiting_for_soc", NOW + timedelta(minutes=1), True, False),
    ("waiting_for_soc", NOW - timedelta(minutes=1), False, False),
    ("waiting_for_soc", None, True, False),
])
def test_only_a_passed_start_survives_a_gap_in_the_inputs(status, start, keep, kept):
    assert keeps_start(status, start, NOW, keep_start=keep) is kept
