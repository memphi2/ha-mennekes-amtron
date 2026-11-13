"""Check deadline planning at the physical current and data boundaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.mennekes_amtron_rtu.device.charging_plan import (
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


def test_plan_starts_late_at_full_current_and_keeps_a_reserve() -> None:
    # 48 kWh need 4.8 h at 16 A on three phases; the start keeps 30 minutes.
    result = plan(8)
    assert result.status == "charging"
    assert result.current_a == 16
    assert result.start_at == pytest.approx(
        NOW + timedelta(hours=8) - timedelta(hours=48 / (16 * 0.621)) - timedelta(minutes=30),
        abs=timedelta(seconds=1),
    )
    assert plan(1).status == "unreachable"
    assert plan(1).current_a == 16


def test_plan_finishes_at_the_minimum_current_once_it_fits() -> None:
    # 48 kWh fit 12.9 h at 6 A: the rest of the time then runs at the minimum.
    assert plan(13).status == "finishing"
    assert plan(13).current_a == 6
    assert plan(12.8).status == "charging"


def test_a_small_charge_starts_when_the_minimum_current_just_fits() -> None:
    result = plan(8, soc_percent=78)
    assert result.status == "finishing"
    assert result.start_at == pytest.approx(
        NOW + timedelta(hours=8) - timedelta(hours=1.6 / (6 * 0.621)),
        abs=timedelta(seconds=1),
    )


def test_a_metered_full_power_replaces_the_nominal_one_within_bounds() -> None:
    nominal = plan(8)
    assert nominal.full_hours == pytest.approx(48 / (16 * 0.621))
    # 7 kW instead of 11 kW: the charge takes longer and starts earlier.
    slower = plan(8, full_power_kw=7.0)
    assert slower.full_hours == pytest.approx(48 / (7.0 * 0.9))
    assert slower.start_at < nominal.start_at
    # More than 110 % or less than 30 % of the nominal power is not believed.
    assert plan(8, full_power_kw=50.0).full_hours == pytest.approx(48 / (11.04 * 1.1 * 0.9))
    assert plan(8, full_power_kw=1.0).full_hours == pytest.approx(48 / (11.04 * 0.3 * 0.9))
    assert plan(1, full_power_kw=7.0).status == "unreachable"
    assert plan(-1, full_power_kw=7.0).full_hours == pytest.approx(48 / 6.3)
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError, match="invalid charge-plan input"):
            plan(8, full_power_kw=bad)


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
    reference = plan(5)
    assert (result.current_a, result.status) == (reference.current_a, reference.status)
    assert finish - result.start_at == NOW + timedelta(hours=5) - reference.start_at


def test_two_phase_vehicle_starts_earlier() -> None:
    assert plan(8, phases=2).start_at < plan(8).start_at
