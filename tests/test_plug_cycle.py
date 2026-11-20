"""The one account of the physical connection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.plug_cycle import (
    UNKNOWN_GRACE_SECONDS,
    PlugCycle,
)
from custom_components.mennekes_amtron_rtu.device.registers import CP_STATE

LAYOUT = CP_STATE.min_layout
START = datetime(2026, 11, 20, 12, tzinfo=UTC)


def poll(evse: EvseState, cp: CpState, **more: object) -> WallboxData:
    return WallboxData(values={"evse_state": int(evse), "cp_state": int(cp), **more})


def test_a_connection_lasts_from_a_confirmed_plug_in_to_a_confirmed_unplug():
    cycle = PlugCycle()
    assert (cycle.present, cycle.id, cycle.scope, cycle.mark) == (None, None, None, None)
    # Unknown before anything was seen: nothing to keep and nothing to doubt.
    assert not cycle.observe(None, now=0).ended
    assert not cycle.observe(False, at=START, now=1).ended
    cycle.observe(True, at=START + timedelta(minutes=5), now=2)
    token, scope = cycle.id, cycle.scope
    assert token is not None
    assert scope is not None
    assert scope != token
    # It began when the wallbox last saw no car, not when the poll came.
    assert cycle.since == START
    cycle.observe(True, now=3)
    assert (cycle.id, cycle.scope, cycle.since) == (token, scope, START)
    assert cycle.observe(False, now=4).ended
    assert (cycle.id, cycle.scope, cycle.since) == (None, None, None)
    cycle.observe(True, at=START + timedelta(hours=1), now=5)
    assert cycle.id not in (None, token)


def test_a_first_sight_of_a_connected_car_starts_with_that_poll():
    cycle = PlugCycle()
    cycle.observe(True, at=START, now=0)
    assert cycle.since == START


def test_a_short_unknown_state_keeps_the_connection_and_a_lasting_one_its_identity_only():
    cycle = PlugCycle()
    cycle.observe(True, now=0)
    token, scope = cycle.id, cycle.scope
    cycle.observe(None, now=10)
    cycle.observe(None, now=10 + UNKNOWN_GRACE_SECONDS - 1)
    assert (cycle.lapsed, cycle.scope) == (False, scope)
    cycle.observe(True, now=80)
    # Seen again in time: the grace starts anew with the next gap.
    cycle.observe(None, now=100)
    cycle.observe(None, now=159)
    assert not cycle.lapsed
    cycle.observe(None, now=160)
    assert cycle.lapsed
    # Still the same plug-in as far as the wallbox says, but nobody can tell
    # whether it is the same car.
    assert (cycle.id, cycle.scope) == (token, None)
    cycle.observe(None, now=1000)
    cycle.observe(True, now=1001)
    assert not cycle.lapsed
    assert cycle.id == token
    assert cycle.scope not in (None, scope)


def test_a_reconnect_counts_as_unknown_from_then_on():
    cycle = PlugCycle()
    cycle.suspect(now=0)
    cycle.observe(True, now=1)
    cycle.suspect(now=10)
    cycle.suspect(now=50)
    cycle.observe(None, now=69)
    assert not cycle.lapsed
    cycle.observe(None, now=70)
    assert cycle.lapsed


def test_a_poll_gives_both_views_of_presence_and_the_wallbox_record():
    cycle = PlugCycle()
    # The EVSE state lags at plug-in: enough to measure, not to permit.
    cycle.read(poll(EvseState.IDLE, CpState.B1), LAYOUT, now=0)
    assert (cycle.present, cycle.metered, cycle.id) == (None, True, None)
    data = poll(EvseState.EV_CONNECTED, CpState.B1, session_duration=30, session_energy=0.5, sessions_total=7)
    data.session_read = data.energy_read = True
    cycle.read(data, LAYOUT, now=1)
    assert (cycle.present, cycle.metered) == (True, True)
    mark = cycle.mark
    assert mark is not None
    assert (mark.duration_s, mark.energy_kwh, mark.count) == (30, 0.5, 7)
    # A poll without the session block keeps what was read before.
    cycle.read(poll(EvseState.CHARGING, CpState.C2), LAYOUT, now=2)
    assert cycle.mark == mark
    # A held value is no evidence, and a failed poll none at all.
    held = WallboxData(
        values={"evse_state": int(EvseState.CHARGING), "cp_state": int(CpState.C2)},
        held=frozenset({"evse_state", "cp_state"}),
    )
    cycle.read(held, LAYOUT, now=3)
    assert cycle.present is None
    cycle.read(None, LAYOUT, now=4)
    assert (cycle.present, cycle.metered) == (None, None)
    cycle.read(None, LAYOUT, now=64)
    assert (cycle.lapsed, cycle.mark) == (True, None)
    # The record of a plug-in that may be over is not continued.
    cycle.read(data, LAYOUT, now=65)
    assert cycle.mark is not None
    assert cycle.mark.at >= mark.at
    cycle.read(poll(EvseState.IDLE, CpState.A1), LAYOUT, now=66)
    assert (cycle.present, cycle.metered, cycle.mark) == (False, False, None)


def test_an_incomplete_wallbox_record_is_no_mark():
    cycle = PlugCycle()
    data = poll(EvseState.EV_CONNECTED, CpState.B1, session_duration=-1, session_energy=0.5, sessions_total=7)
    data.session_read = data.energy_read = True
    cycle.read(data, LAYOUT, now=0)
    assert cycle.reading == {"count": 7}
    assert cycle.mark is None


def test_forgetting_leaves_nothing():
    cycle = PlugCycle()
    cycle.observe(False, at=START, now=0)
    cycle.observe(True, now=1)
    cycle.forget()
    assert (cycle.present, cycle.metered, cycle.id, cycle.since, cycle.lapsed) == (None, None, None, None, False)
    cycle.observe(True, at=START + timedelta(hours=2), now=2)
    assert cycle.since == START + timedelta(hours=2)
