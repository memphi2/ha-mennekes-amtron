"""Only coherent wallbox evidence may restore a vehicle plan's plug-in."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device.session_mark import (
    SessionMark,
    session_time,
)

MARK = SessionMark(datetime(2026, 11, 14, tzinfo=UTC), 3600, 4.0, 7, "a" * 64)


@pytest.mark.parametrize("value", [None, True, "x" * 65, "not-a-date", "2026-11-14T00:00:00", "0001-01-01T00:00:00+14:00"])
def test_invalid_or_ambiguous_times_cannot_restore_a_plan(value):
    with pytest.raises(ValueError, match="session time"):
        session_time(value)


def test_round_trip_and_offset_normalization():
    assert SessionMark.restore(MARK.snapshot()) == MARK
    assert session_time("2026-11-14T01:00:00+01:00") == MARK.at
    assert MARK.continues(replace(MARK, at=MARK.at + timedelta(minutes=10), duration_s=4200, energy_kwh=5))


@pytest.mark.parametrize("change", [
    {"duration_s": True}, {"duration_s": -1}, {"duration_s": 2**32},
    {"count": False}, {"count": -1}, {"count": 2**32},
    {"energy_kwh": True}, {"energy_kwh": "4"}, {"energy_kwh": float("nan")},
    {"energy_kwh": float("inf")}, {"energy_kwh": -1},
    {"context": None}, {"context": "a" * 63}, {"context": "Z" * 64},
])
def test_damaged_session_evidence_is_rejected(change):
    with pytest.raises(ValueError, match="saved session"):
        SessionMark.restore({**MARK.snapshot(), **change})


@pytest.mark.parametrize("raw", [None, {}, {**MARK.snapshot(), "extra": 1}])
def test_no_partial_or_foreign_session_records(raw):
    with pytest.raises(ValueError, match="session fields"):
        SessionMark.restore(raw)


@pytest.mark.parametrize("change", [
    {"context": "b" * 64}, {"count": 8}, {"duration_s": 0}, {"energy_kwh": 0},
    {"at": MARK.at + timedelta(hours=1)},
])
def test_replug_counter_reset_and_clock_jump_do_not_prove_continuity(change):
    assert not MARK.continues(replace(MARK, **change))
