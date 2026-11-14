"""Power learning never joins different cars, settings or invalid meter windows."""
from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device.power_window import PowerWindow


def sample(window, **overrides):
    values = {"at": 1000.0, "total": 100.0, "phases": 3, "current": 16.0, "vehicle": "car"}
    return window.sample(**(values | overrides))


def test_window_ignores_duplicate_reads_and_uses_actual_elapsed_time():
    window = PowerWindow()
    assert sample(window) is None
    assert sample(window) is None
    assert sample(window, at=1119, total=100.1) is None
    assert sample(window, at=1120, total=100.2) == pytest.approx(6)
    assert window.start == (1120, 100.2, 3, 16)
    window.reset()
    assert window.start is window.vehicle is None


@pytest.mark.parametrize("changes", [
    {"vehicle": "other"}, {"phases": 1}, {"current": 10},
    {"at": 900}, {"total": 99}, {"total": -1},
    {"at": float("nan")}, {"total": float("inf")},
])
def test_discontinuities_do_not_teach_a_power(changes):
    window = PowerWindow()
    sample(window)
    assert sample(window, **({"at": 1120, "total": 100.2} | changes)) is None
