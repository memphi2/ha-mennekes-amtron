"""Check deadline planning at the physical current and data boundaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.mennekes_amtron_rtu.charging_plan import (
    plan_current,
    usable_capacity,
)

NOW = datetime(2026, 11, 6, 6, tzinfo=UTC)


def plan(hours: float, **overrides: float) -> object:
    values = {
        "now": NOW, "deadline": NOW + timedelta(hours=hours), "soc_percent": 20.0,
        "target_percent": 80.0, "capacity_kwh": 80.0, "phases": 3, "max_current_a": 16.0,
    }
    values.update(overrides)
    return plan_current(**values)


def test_capacity_requires_real_energy_units_and_a_bounded_usable_value() -> None:
    assert usable_capacity(80000, "Wh") == 80
    assert usable_capacity(80, "kWh") == 80
    for value, unit in ((80, "%"), (0, "kWh"), (251, "kWh"), (float("nan"), "kWh")):
        assert usable_capacity(value, unit) is None
    assert usable_capacity(True) is None
    assert usable_capacity("80") is None
    assert usable_capacity(10**1000) is None


def test_plan_paces_current_within_physical_limits() -> None:
    result = plan(8)
    assert result.status == "paced"
    assert result.current_a == 9.7
    assert result.required_kwh == 48
    assert plan(24).status == "early_at_minimum"
    assert plan(24).current_a == 6
    assert plan(1).status == "unreachable"
    assert plan(1).current_a == 16


def test_plan_reports_reached_and_late_without_inventing_a_pause() -> None:
    assert plan(8, soc_percent=80).current_a is None
    assert plan(-1).status == "late"


def test_invalid_phase_capacity_or_clock_cannot_drive_a_current() -> None:
    with pytest.raises(ValueError, match="invalid charge-plan input"):
        plan(8, phases=0)
    with pytest.raises(ValueError, match="invalid charge-plan input"):
        plan(8, capacity_kwh=0)
    with pytest.raises(ValueError, match="aware timestamp"):
        plan_current(
            now=NOW.replace(tzinfo=None), deadline=NOW,
            soc_percent=20, target_percent=80, capacity_kwh=80,
            phases=3, max_current_a=16,
        )


def test_dst_transition_uses_elapsed_hours_instead_of_wall_clock() -> None:
    zone = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 25, 0, tzinfo=zone)
    finish = datetime(2026, 10, 25, 4, tzinfo=zone)
    result = plan_current(
        now=start, deadline=finish, soc_percent=20, target_percent=80,
        capacity_kwh=80, phases=3, max_current_a=16,
    )
    assert result == plan(5)


def test_two_phase_vehicle_receives_enough_current() -> None:
    assert plan(8, phases=2).current_a == 14.5
