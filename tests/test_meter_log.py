"""The wallbox energy of any observed time span."""

from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device import meter_log
from custom_components.mennekes_amtron_rtu.device.meter_log import MeterLog


def filled(*readings: tuple[float, float]) -> MeterLog:
    log = MeterLog()
    for at, kwh in readings:
        log.observe(at, kwh)
    return log


def test_a_span_between_flat_readings_is_exact_and_inside_a_charge_interpolated() -> None:
    log = filled((0, 100), (30, 100), (60, 100), (90, 101), (120, 102), (150, 102), (180, 102))
    assert log.first_at == 0
    # The unchanged meter keeps its first and last reading only.
    assert log._times == [0, 60, 90, 120, 180]
    assert log.energy_between(10, 170) == pytest.approx(2)
    assert log.energy_between(75, 105) == pytest.approx(1)
    assert log.energy_between(120, 180) == 0


def test_a_span_that_is_not_covered_is_unknown() -> None:
    log = filled((100, 50), (130, 51))
    assert MeterLog().first_at is None
    assert MeterLog().energy_between(0, 1) is None
    assert log.energy_between(90, 120) is None
    assert log.energy_between(110, 140) is None
    assert log.energy_between(120, 110) is None
    assert log.energy_between(100, 130) == pytest.approx(1)


def test_energy_across_a_long_unobserved_stretch_places_no_boundary() -> None:
    log = filled((0, 10), (2000, 15), (2030, 15))
    assert log.energy_between(0, 2030) == pytest.approx(5)
    assert log.energy_between(1000, 2030) is None


def test_a_meter_that_goes_back_or_a_clock_step_starts_the_log_again() -> None:
    log = filled((0, 10), (30, 11), (60, 5))
    assert log._times == [60]
    log.observe(50, 5)
    assert log._times == [50]
    # The same moment again, bad values: ignored.
    log.observe(50, 6)
    for at, kwh in ((float("nan"), 1), (70, float("inf")), (70, -1)):
        log.observe(at, kwh)
    assert log._values == [5]


def test_the_log_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meter_log, "_MAX_READINGS", 3)
    log = filled((0, 1), (1, 2), (2, 3), (3, 4))
    assert log._times == [1, 2, 3]
