"""Finishing is bounded by the deadline, not a full-current control pulse."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.charging_plan import plan_current

NOW = datetime(2026, 11, 12, 6, tzinfo=UTC)


def plan(energy, seconds, *, phases=3, maximum=16):
    return plan_current(
        now=NOW, deadline=NOW + timedelta(seconds=seconds),
        soc_percent=80 - energy, target_percent=80, capacity_kwh=100,
        phases=phases, max_current_a=maximum,
    )


@pytest.mark.parametrize("phases", [1, 2, 3])
@pytest.mark.parametrize("offset", [-0.001, 0, 0.001])
def test_exact_minimum_boundary_ignores_datetime_rounding(phases, offset):
    energy = 230 * phases * 0.9 * 6 / 1000
    result = plan(energy, 3600 + offset, phases=phases)
    assert result.current_a == 6
    assert result.status == "finishing"


@pytest.mark.parametrize("phases", [1, 2, 3])
def test_crossover_inside_next_write_window_uses_intermediate_current(phases):
    # One hour left; 6 A for the hour plus 2 A for the first five minutes.
    energy = 230 * phases * 0.9 / 1000 * (6 + 2 / 12)
    result = plan(energy, 3600, phases=phases)
    assert result.status == "charging"
    # Upward whole-amp rounding can conservatively add an amp at a float edge.
    assert 8 <= result.current_a <= 9
    assert result.current_a < 16


@pytest.mark.parametrize("maximum", [6, 6.5, 10, 16, 32])
def test_current_remains_inside_the_configured_range(maximum):
    for energy in (0.001, 0.1, 1, 10, 60):
        for seconds in (0.001, 30, 299, 300, 1800, 3600):
            result = plan(energy, seconds, maximum=maximum)
            assert 6 <= result.current_a <= maximum


def test_last_partial_window_does_not_spend_a_full_five_minutes():
    # Three minutes left and 8 A would suffice: 16 A would finish halfway.
    result = plan(8 * 0.621 * 3 / 60, 180)
    assert 8 <= result.current_a <= 9
    assert result.current_a < 16


def test_missing_the_full_current_boundary_by_rounding_is_not_unreachable():
    result = plan(16 * 0.621, 3600 - 0.001)
    assert result.status == "charging"
    assert result.current_a == 16


@pytest.mark.parametrize("phases", [1, 2, 3])
@pytest.mark.parametrize("maximum", [10, 16, 32])
@pytest.mark.parametrize("energy", [2, 15, 48])
def test_fresh_progress_with_five_minute_control_finishes_close_to_departure(phases, maximum, energy):
    """Model test, not a promise about cloud latency or battery efficiency."""
    coefficient = 230 * phases * 0.9 / 1000
    deadline = NOW + timedelta(hours=energy / (maximum * coefficient) + 2)
    initial = plan_current(
        now=NOW, deadline=deadline, soc_percent=80 - energy,
        target_percent=80, capacity_kwh=100, phases=phases,
        max_current_a=maximum,
    )
    now = initial.start_at
    remaining = energy
    previous_current = None
    for _ in range(1000):
        result = plan_current(
            now=now, deadline=deadline, soc_percent=80 - remaining,
            target_percent=80, capacity_kwh=100, phases=phases,
            max_current_a=maximum,
        )
        current = result.current_a
        assert current is not None
        assert 6 <= current <= maximum
        if previous_current is not None:
            assert current <= previous_current
        finish = now + timedelta(hours=remaining / (current * coefficient))
        if finish <= now + timedelta(minutes=5):
            # Whole-amp rounding contributes at most 50 seconds at 6 A;
            # tolerate one minute, not a several-minute 16/32 A pulse.
            assert abs((deadline - finish).total_seconds()) <= 61
            break
        remaining -= current * coefficient * 5 / 60
        now += timedelta(minutes=5)
        previous_current = current
    else:
        pytest.fail("planner did not finish within the bounded simulation")
