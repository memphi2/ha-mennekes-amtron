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
    log = filled((0, 1), (100, 2), (200, 3), (300, 4))
    assert log._times == [100, 200, 300]


def test_a_long_charge_at_a_short_poll_interval_keeps_its_plug_in() -> None:
    log = MeterLog()
    log.observe(0, 100)
    log.observe(1000, 100)
    # Twelve hours of charging, read every two seconds.
    for step in range(1, 21601):
        log.observe(1000 + 2 * step, 100 + step * 0.001)
    log.observe(50000, 121.6)
    assert len(log._times) < 2500
    # Where the rise begins and ends, the readings are exact.
    assert log._times[:2] == [0, 1000]
    assert log._times[-2:] == [44200, 50000]
    assert log.energy_between(1000, 44200) == pytest.approx(21.6)
    assert log.energy_between(500, 46000) == pytest.approx(21.6)
    assert log.energy_between(1000, 1000 + 7200) == pytest.approx(3.6)
    # A meter that counts in steps slower than it is read thins out as well.
    stepped = MeterLog()
    stepped.observe(0, 100)
    stepped.observe(1000, 100)
    for step in range(1, 21601):
        stepped.observe(1000 + 2 * step, 100 + (step // 4) * 0.01)
    stepped.observe(50000, 154)
    assert len(stepped._times) < 3000
    assert stepped._times[:2] == [0, 1006]
    assert stepped._times[-2:] == [44200, 50000]
    assert stepped.energy_between(500, 46000) == pytest.approx(54)


def test_the_log_keeps_only_what_the_running_and_the_last_cycle_need() -> None:
    meter = ChargeMeter()
    for at in range(0, 5000, 100):
        meter.observe(at, present=False, reading=10 + at / 1000)
    # Nobody plugged in: nothing older than the reach of one boundary is kept.
    assert meter.log._times[0] == 4300
    meter.observe(5000, present=True, charging=True, reading=15)
    for at in range(5100, 9000, 100):
        meter.observe(at, present=True, charging=True, reading=10 + at / 1000)
    meter.observe(9000, present=False, reading=19)
    assert meter.log._times[0] == 4400
    assert meter.log.energy_between(*meter.last) == pytest.approx(4)
    # The next cycle is the running one, and the one before stays measurable.
    meter.observe(9100, present=True, reading=19)
    assert meter.log._times[0] == 4400
    meter.observe(9200, present=False, reading=19)
    assert meter.last == (9100, 9200)
    assert meter.log._times[0] == 8500


def test_a_clock_that_moves_back_ends_the_cycle_unmeasured() -> None:
    meter = ChargeMeter()
    meter.observe(1000, present=False, reading=10)
    meter.observe(1010, present=True, charging=True, reading=10)
    assert meter.annotate(1010, "car")
    meter.observe(900, present=True, charging=True, reading=11)
    assert (meter.plugged_at, meter.note, meter.known_since) == (None, None, 900)
    meter.observe(905, present=False, reading=11)
    assert meter.last is None
    parse_charge_meter(meter.snapshot())
    # The same after a restart: a saved state from a later clock is not continued.
    meter.observe(910, present=True, reading=11)
    restarted = ChargeMeter()
    restarted.restore(meter.dump())
    restarted.observe(800, present=True, reading=11)
    assert restarted.plugged_at is None


def test_a_number_too_large_for_a_time_is_refused() -> None:
    with pytest.raises(ValueError, match="invalid charge meter time"):
        meter_log.moment(10**400)
    saved = ChargeMeter().dump() | {"known_at": 10**400}
    with pytest.raises(ValueError, match="invalid charge meter time"):
        ChargeMeter().restore(saved)


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
        "known_since": 10, "present": True,
        "plugged_at": 30, "charging_at": 40, "note": None,
        "last": None, "last_note": None,
    }
    # One poll without an answer is bridged.
    meter.observe(60, present=None)
    meter.observe(70, present=False, reading=105)
    assert meter.snapshot() == {
        "known_since": 10, "present": False,
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
    # A clock that stepped back knows the state only from there on.
    meter.observe(500, present=None)
    meter.observe(510, present=False)
    assert meter.known_since == 510
    assert meter.present is False


@pytest.mark.parametrize("changes", [
    {"extra": 1}, {"last": [1]}, {"last": (1, 2)}, {"last": [2, 1]}, {"last": [None, 2]},
    {"plugged_at": "1"}, {"charging_at": True}, {"known_since": float("nan")},
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
