"""Explain tri-state vehicle evidence without relaxing the grant policy."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.plug_charge_explanation import (
    explain_detection,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources import VehicleDetection

NOW = datetime(2026, 11, 12, 20, tzinfo=UTC)


@pytest.mark.parametrize(("connected", "home", "reason", "matches"), [
    (True, True, "matched", True),
    (False, True, "vehicle_unplugged", False),
    (True, False, "vehicle_away", False),
    (None, True, "cable_unknown_or_stale", None),
    (True, None, "location_unknown_or_stale", None),
])
def test_each_signal_explains_the_actual_tri_state_match(connected, home, reason, matches):
    evidence = VehicleDetection(connected, home, NOW)
    result = explain_detection([(True, evidence)], NOW)
    check = result["checks"][0]
    assert check["reason"] == reason
    assert check["matches"] is matches
    assert check["selected"] is True
    assert check["report_current"] is True
    assert check["slot"] == 1


@pytest.mark.parametrize(("signals", "reason"), [
    ([], "detection_not_configured"),
    ([(True, True)], "vehicle_detected"),
    ([(True, True), (True, True)], "multiple_vehicles_match"),
    ([(True, True), (None, True)], "vehicle_evidence_unknown"),
    ([(False, True), (True, False)], "no_vehicle_matches"),
])
def test_selection_reasons_include_unknown_competitors(signals, reason):
    result = explain_detection([(False, VehicleDetection(*signal)) for signal in signals], NOW)
    assert result["reason"] == reason
    assert len(result["checks"]) == len(signals)


@pytest.mark.parametrize(("stamp", "since", "current"), [
    (NOW, NOW, True),
    (NOW - timedelta(minutes=2), NOW, True),
    (NOW - timedelta(minutes=2, seconds=1), NOW, False),
    (None, NOW, None),
    (NOW.replace(tzinfo=None), NOW, None),
    (NOW, None, None),
])
def test_session_report_is_bounded_without_a_ticking_age(stamp, since, current):
    result = explain_detection([(True, VehicleDetection(True, True, stamp))], since)
    assert result["checks"][0]["report_current"] is current
    assert set(result["checks"][0]) == {
        "slot", "selected", "connected", "home", "parked_home", "matches", "report_current", "reason",
    }


def test_parked_vehicle_with_a_matching_cable_change_is_explained_as_matched():
    evidence = VehicleDetection(True, None, NOW, parked_home=True, changed_at=NOW)
    result = explain_detection([(True, evidence)], NOW)
    assert result["reason"] == "vehicle_detected"
    assert result["checks"][0]["reason"] == "matched"
