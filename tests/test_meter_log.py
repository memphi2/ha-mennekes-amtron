"""The wallbox energy of any observed time span."""

from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device import meter_log
from custom_components.mennekes_amtron_rtu.device.meter_log import (
    ChargeMeter,
    MeterLog,
    parse_charge_meter,
)


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


def test_the_service_keeps_only_a_plug_cycle_it_saw_from_end_to_end() -> None:
    meter = ChargeMeter()
    # Started with a car already plugged in: this cycle has no beginning.
    meter.observe(10, present=True, charging=True, reading=100)
    meter.observe(20, present=False, reading=101)
    assert meter.last is None
    meter.observe(30, present=True)
    meter.observe(40, present=True, charging=True, reading=101)
    meter.observe(50, present=True, charging=True, reading=104)
    assert meter.snapshot() == {
        "first_reading_at": 10, "known_since": 10, "present": True,
        "plugged_at": 30, "charging_at": 40, "note": None,
        "last": None, "last_note": None,
    }
    # One poll without an answer is bridged.
    meter.observe(60, present=None)
    meter.observe(70, present=False, reading=105)
    assert meter.snapshot() == {
        "first_reading_at": 10, "known_since": 10, "present": False,
        "plugged_at": None, "charging_at": None, "note": None,
        "last": [30, 70], "last_note": None,
    }
    assert meter.log.energy_between(*meter.last) == pytest.approx(4)
    assert parse_charge_meter(meter.snapshot())["last"] == (30, 70)
    # A long silence may hide an unplug: that cycle is not kept.
    meter.observe(80, present=True)
    meter.observe(90, present=None)
    meter.observe(900, present=True)
    assert meter.known_since == 900
    meter.observe(910, present=False)
    assert meter.last == (30, 70)
    # A clock that stepped back measures the gap from there.
    meter.observe(500, present=None)
    meter.observe(510, present=False)
    assert meter.known_since == 900
    assert meter.present is False


@pytest.mark.parametrize("changes", [
    {"extra": 1}, {"last": [1]}, {"last": (1, 2)}, {"last": [2, 1]}, {"last": [None, 2]},
    {"plugged_at": "1"}, {"charging_at": True}, {"first_reading_at": float("nan")},
    {"present": 1}, {"known_since": []}, {"note": ""}, {"note": 5}, {"last_note": "x" * 129},
])
def test_a_reported_charge_meter_is_checked(changes) -> None:
    raw = {**ChargeMeter().snapshot(), **changes}
    with pytest.raises(ValueError, match=r"charge meter|plug cycle"):
        parse_charge_meter(raw)
    with pytest.raises(ValueError, match="charge meter"):
        parse_charge_meter(None)


def test_a_note_stays_with_the_cycle_it_was_given_to() -> None:
    meter = ChargeMeter()
    meter.observe(10, present=False)
    assert not meter.annotate(10, "car")
    meter.observe(20, present=True)
    assert not meter.annotate(19, "car")
    assert meter.annotate(20, "car")
    with pytest.raises(ValueError, match="note"):
        meter.annotate(20, "")
    meter.observe(30, present=False)
    assert (meter.note, meter.last, meter.last_note) == (None, (20, 30), "car")
    # The next cycle starts unnamed, and may be told that it is nobody's.
    meter.observe(40, present=True)
    assert meter.note is None
    assert meter.annotate(40, "car")
    assert meter.annotate(40, None)
    meter.observe(50, present=False)
    assert meter.last_note is None


def restarted(meter: ChargeMeter) -> ChargeMeter:
    again = ChargeMeter()
    again.restore(meter.dump())
    return again


def test_a_restarted_service_goes_on_measuring_the_same_cycle() -> None:
    meter = ChargeMeter()
    meter.observe(10, present=False, reading=100)
    meter.observe(20, present=True, charging=True, reading=100)
    meter.annotate(20, "car")
    meter.observe(30, present=True, charging=True, reading=102)
    again = restarted(meter)
    assert again.dump() == meter.dump()
    # A short restart is bridged like a poll without an answer ...
    again.observe(100, present=True, charging=True, reading=110)
    again.observe(110, present=False, reading=110)
    assert (again.last, again.last_note) == ((20, 110), "car")
    assert again.log.energy_between(20, 110) == pytest.approx(10)
    # ... a long one may hide an unplug, and another car.
    late = restarted(meter)
    late.observe(400, present=True, charging=True, reading=110)
    assert (late.plugged_at, late.note, late.known_since) == (None, None, 400)
    late.observe(410, present=False)
    assert late.last is None
    # A service that never polled has nothing to bridge.
    fresh = restarted(ChargeMeter())
    fresh.observe(5000, present=True)
    assert fresh.known_since == 5000


@pytest.mark.parametrize("changes", [
    {"extra": 1}, {"known_at": "1"}, {"readings": None}, {"readings": [[1]]},
    {"readings": [[1, 2], [1, 3]]}, {"readings": [[1, 2], [2, 1]]}, {"readings": [[1, -1]]},
    {"readings": [[None, 1]]}, {"readings": [1]}, {"present": 1},
])
def test_a_saved_charge_meter_is_checked_as_a_whole(changes) -> None:
    meter = ChargeMeter()
    meter.observe(10, present=True, reading=100)
    saved = {**meter.dump(), **changes}
    again = ChargeMeter()
    with pytest.raises(ValueError, match=r"charge meter|meter log|meter reading"):
        again.restore(saved)
    assert again.dump() == ChargeMeter().dump()


def test_more_saved_readings_than_the_log_holds_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meter_log, "_MAX_READINGS", 1)
    with pytest.raises(ValueError, match="meter log"):
        meter_log.MeterLog().restore([[1, 1], [2, 2]])
