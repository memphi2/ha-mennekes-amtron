"""Completed sessions require measured boundaries, never inferred reset behavior."""

from __future__ import annotations

from uuid import UUID

import pytest

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
    SessionHistory,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState

IDLE = EvseState.IDLE
CHARGING = EvseState.CHARGING
CONNECTED = EvseState.EV_CONNECTED
READY = EvseState.READY_TO_CHARGE


def reading(state=IDLE, total=100.0, *, fresh=True, cp=None):
    if cp is None:
        cp = CpState.A1 if state == IDLE else CpState.C2 if state == CHARGING else CpState.B1
    return WallboxData(
        values={"evse_state": state, "cp_state": cp, "energy_total": total},
        energy_read=fresh,
    )


def complete(history=None, *, offset=0, baseline=100):
    history = history or SessionHistory()
    assert not history.observe(reading(IDLE, baseline), now=offset + 10)
    assert not history.observe(reading(CONNECTED, baseline), now=offset + 20)
    assert not history.observe(reading(CHARGING, baseline + 1), now=offset + 30)
    assert not history.observe(reading(CHARGING, baseline + 8), now=offset + 40)
    assert not history.observe(reading(READY, baseline + 10), now=offset + 50)
    assert history.observe(reading(IDLE, baseline + 10), now=offset + 60)
    return history


def test_complete_cycle_uses_lifetime_energy_and_observed_time_windows():
    history = complete()
    session = history.latest
    assert session is not None
    assert str(UUID(session.session_id)) == session.session_id
    assert session.wallbox_kwh == 10
    assert (session.connected_at, session.disconnected_at) == (10, 60)
    assert (session.started_after, session.started_before) == (20, 30)
    assert (session.ended_after, session.ended_before) == (40, 50)
    assert CompletedSession.parse(session.as_dict()) == session
    assert not history.observe(reading(IDLE, 110), now=70)
    assert history.latest is session


def test_first_connected_sample_may_already_charge_and_last_sample_may_unplug():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=10)
    history.observe(reading(CHARGING, 103), now=20)
    assert history.observe(reading(IDLE, 105), now=30)
    assert history.latest is not None
    assert (history.latest.started_after, history.latest.started_before) == (10, 20)
    assert (history.latest.ended_after, history.latest.ended_before) == (20, 30)
    assert history.latest.wallbox_kwh == 5


def test_pauses_and_confirmed_connected_faults_never_finish_the_cycle():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=10)
    history.observe(reading(CHARGING, 101), now=20)
    history.observe(reading(CONNECTED, 105), now=30)
    assert history.latest is None
    history.observe(reading(CHARGING, 108), now=40)
    history.observe(reading(EvseState.ERROR, 110), now=50)
    history.observe(reading(EvseState.SERVICE_MODE, 110), now=60)
    assert history.latest is None
    history.observe(reading(READY, 110), now=70)
    assert history.observe(reading(IDLE, 110), now=80)
    assert history.latest.wallbox_kwh == 10
    assert (history.latest.started_after, history.latest.started_before) == (10, 20)
    assert (history.latest.ended_after, history.latest.ended_before) == (40, 70)


def test_cached_meter_progress_does_not_change_storage_or_prove_a_final_value():
    history = complete()
    saved = history.snapshot()
    history.observe(reading(IDLE, 110, fresh=False), now=70)
    history.observe(reading(CONNECTED, 110, fresh=False), now=80)
    history.observe(reading(CHARGING, 110, fresh=False), now=90)
    assert history.snapshot() == saved
    assert not history.observe(reading(IDLE, 110, fresh=False), now=100)
    assert history.snapshot() == saved  # The final meter must be read, not cached.
    assert history.observe(reading(IDLE, 113), now=160)
    assert history.latest.wallbox_kwh == 3
    assert history.latest.disconnected_at == 100


def test_failed_final_meter_cannot_reuse_the_previous_sessions_start_as_a_new_anchor():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=10)
    history.observe(reading(CHARGING, 105), now=20)
    history.observe(reading(IDLE, 105, fresh=False), now=30)
    history.observe(reading(CHARGING, 115), now=40)
    assert not history.observe(reading(IDLE, 120), now=50)
    assert history.latest is None
    assert complete(history, offset=50, baseline=120).latest.wallbox_kwh == 10


@pytest.mark.parametrize("interrupt", [None, WallboxData(values={"evse_state": IDLE})])
def test_one_failed_poll_or_unknown_state_keeps_the_cycle(interrupt):
    history = complete()
    history.observe(reading(CONNECTED, 110), now=70)
    history.observe(reading(CHARGING, 112), now=80)
    assert not history.observe(interrupt, now=90)
    history.observe(reading(CHARGING, 118), now=100)
    assert history.observe(reading(IDLE, 120), now=110)
    assert history.latest.wallbox_kwh == 10


@pytest.mark.parametrize("interrupt", [
    reading(EvseState.NOT_INITIALIZED),
    reading(IDLE, cp=CpState.C2),
    reading(CHARGING, -1),
    reading(CHARGING, float("nan")),
    reading(CHARGING, True),
    reading(CHARGING, 10**1000),
    reading(CHARGING, 99),
])
def test_gap_unknown_or_bad_meter_invalidates_current_cycle_until_unplug(interrupt):
    history = complete()
    history.observe(reading(CONNECTED, 110), now=70)
    history.observe(reading(CHARGING, 112), now=80)
    assert not history.observe(interrupt, now=90)
    history.observe(reading(CHARGING, 118), now=100)
    assert history.observe(reading(IDLE, 120), now=110)
    assert history.latest is None
    assert complete(history, offset=110, baseline=120).latest.wallbox_kwh == 10


@pytest.mark.parametrize("bad_time", [0, -1, True, "90", float("nan"), float("inf"), 10**1000, 253402300800])
def test_clock_rollback_and_invalid_clock_never_complete_a_false_session(bad_time):
    history = complete()
    history.observe(reading(CHARGING, 111), now=70)
    assert not history.observe(reading(CHARGING, 115), now=bad_time)
    assert history.observe(reading(IDLE, 120), now=100)
    assert history.latest is None


def test_missing_fresh_idle_anchor_and_startup_mid_charge_remain_incomplete():
    for prefix in ([], [reading(IDLE, 100, fresh=False)], [None, reading(CONNECTED, 100)]):
        history = SessionHistory()
        for index, row in enumerate(prefix):
            history.observe(row, now=index)
        history.observe(reading(CHARGING, 110), now=10)
        assert not history.observe(reading(IDLE, 120), now=20)
        assert history.latest is None


def test_clock_correction_allows_a_new_observed_cycle_without_waiting_for_old_time():
    history = complete(offset=1000)
    assert not history.observe(reading(IDLE, 110), now=10)
    assert complete(history, offset=10, baseline=110).latest.wallbox_kwh == 10


def test_noncharging_counter_increase_before_first_seen_charge_is_incomplete():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=10)
    history.observe(reading(CONNECTED, 101), now=20)
    history.observe(reading(CHARGING, 105), now=30)
    assert not history.observe(reading(IDLE, 110), now=40)
    assert history.latest is None


def test_energy_increase_without_observed_connection_invalidates_old_latest():
    history = complete()
    assert history.observe(reading(IDLE, 115), now=70)
    assert history.latest is None
    assert not history.observe(reading(IDLE, 115), now=80)


def test_energy_increase_during_idle_poll_outage_does_not_keep_an_older_latest_charge():
    history = complete()
    assert not history.observe(None, now=70)
    assert history.observe(reading(IDLE, 115), now=80)
    assert history.latest is None


@pytest.mark.parametrize("final", [reading(IDLE, None), reading(IDLE, 0)])
def test_failed_reset_or_cached_final_meter_does_not_reuse_last_good_sample(final):
    history = complete()
    history.observe(reading(CHARGING, 115), now=70)
    assert history.observe(final, now=80)
    assert history.latest is None


def test_plug_in_without_charging_keeps_the_last_completed_charge():
    history = complete()
    previous = history.latest
    history.observe(reading(CONNECTED, 110), now=70)
    assert not history.observe(reading(IDLE, 110), now=80)
    assert history.latest == previous


def test_plug_in_without_charging_closed_later_keeps_the_last_charge():
    history = complete()
    previous = history.latest
    history.observe(reading(CONNECTED, 110), now=70)
    history.observe(reading(IDLE, 110, fresh=False), now=80)
    assert not history.observe(reading(IDLE, 110), now=90)
    assert history.latest == previous


def test_zero_energy_charging_does_not_create_a_completed_charge():
    history = complete()
    history.observe(reading(CHARGING, 110), now=70)
    assert history.observe(reading(IDLE, 110), now=80)
    assert history.latest is None


def test_partial_progress_is_not_persisted_and_never_restored_as_complete():
    history = complete()
    saved = history.snapshot()
    history.observe(reading(CHARGING, 112), now=70)
    history.observe(reading(CHARGING, 115), now=80)
    assert history.snapshot() == saved
    restored = SessionHistory.restore(saved)
    assert restored.latest == history.latest
    restored.observe(reading(CHARGING, 118), now=90)
    assert restored.observe(reading(IDLE, 120), now=100)
    assert restored.latest is None
    assert saved["latest"] is not None


def test_cp_optional_layout_uses_evse_without_inventing_disconnects_from_faults():
    history = SessionHistory(cp_supported=False)
    history.observe(WallboxData(values={"evse_state": IDLE, "energy_total": 100}, energy_read=True), now=10)
    history.observe(WallboxData(values={"evse_state": CHARGING, "energy_total": 105}, energy_read=True), now=20)
    assert history.observe(WallboxData(values={"evse_state": IDLE, "energy_total": 110}, energy_read=True), now=30)
    assert SessionHistory.restore(history.snapshot(), cp_supported=False).latest == history.latest


def test_history_restores_empty_snapshot():
    assert SessionHistory.restore(SessionHistory().snapshot()).latest is None


@pytest.mark.parametrize("raw", [
    None, [], {}, {"version": True, "latest": None}, {"version": 2, "latest": None},
    {"version": 1, "latest": None, "extra": 1}, {"version": 1, "latest": {}},
])
def test_history_rejects_invalid_stored_data(raw):
    with pytest.raises(ValueError, match=r"session|fields"):
        SessionHistory.restore(raw)


@pytest.mark.parametrize("changes", [
    {"session_id": "x"}, {"session_id": 1}, {"session_id": "x" * 36},
    {"session_id": "ABCDEFAB-0000-0000-0000-000000000000"},
    {"started_before": 5}, {"ended_before": 90}, {"started_after": 35},
    {"ended_after": 25}, {"wallbox_kwh": 0}, {"wallbox_kwh": -1},
    {"wallbox_kwh": True}, {"wallbox_kwh": float("nan")},
    {"connected_at": 253402300800}, {"disconnected_at": float("inf")},
    {"disconnected_at": "60"}, {"extra": 1},
])
def test_completed_session_parse_rejects_invalid_values_and_order(changes):
    raw = complete().latest.as_dict() | changes
    with pytest.raises(ValueError, match=r"session|range"):
        CompletedSession.parse(raw)


@pytest.mark.parametrize("raw", [None, [], {}, ""])
def test_completed_session_parse_rejects_invalid_shape(raw):
    with pytest.raises(ValueError, match="fields"):
        CompletedSession.parse(raw)


def test_same_instant_positive_energy_is_not_a_completed_charge():
    raw = {
        "session_id": str(UUID(int=1)), "connected_at": 10,
        "disconnected_at": 10, "started_after": 10, "started_before": 10,
        "ended_after": 10, "ended_before": 10, "wallbox_kwh": 1,
    }
    with pytest.raises(ValueError, match="duration"):
        CompletedSession.parse(raw)


def _real_poll_cycle(history, *, cp_first_on_unplug):
    """Real polls: the meter is fresh only on an EVSE change or a slow read."""
    b1, a1 = CpState.B1, CpState.A1
    history.observe(reading(IDLE, 100), now=0)
    history.observe(reading(IDLE, 100, fresh=False, cp=b1), now=5)  # CP leads the EVSE
    history.observe(reading(CONNECTED, 100), now=10)
    history.observe(reading(CHARGING, 100), now=15)
    history.observe(reading(CHARGING, 101, fresh=False), now=20)
    history.observe(reading(CHARGING, 108), now=75)
    history.observe(reading(READY, 110), now=180)
    if cp_first_on_unplug:
        history.observe(reading(READY, 110, fresh=False, cp=a1), now=205)
        return history.observe(reading(IDLE, 110), now=210)
    history.observe(reading(IDLE, 110, cp=b1), now=205)  # EVSE leads the CP
    history.observe(reading(IDLE, 110, fresh=False), now=210)
    return history.observe(reading(IDLE, 110), now=270)


@pytest.mark.parametrize("cp_first_on_unplug", [True, False])
def test_transitional_plug_states_and_a_later_final_meter_still_complete(cp_first_on_unplug):
    history = SessionHistory()
    assert _real_poll_cycle(history, cp_first_on_unplug=cp_first_on_unplug)
    session = history.latest
    assert session is not None
    assert session.wallbox_kwh == 10
    assert session.disconnected_at == 210


def test_unknown_presence_longer_than_two_minutes_still_invalidates():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=0)
    history.observe(reading(CHARGING, 101), now=10)
    for now in range(20, 180, 20):
        history.observe(reading(EvseState.NOT_INITIALIZED, 105), now=now)
    history.observe(reading(CHARGING, 106), now=160)
    history.observe(reading(IDLE, 110), now=170)
    assert history.latest is None


def test_replug_before_a_final_meter_never_merges_two_connections():
    history = complete()
    history.observe(reading(CONNECTED, 110), now=70)
    history.observe(reading(CHARGING, 115), now=80)
    history.observe(reading(IDLE, 115, fresh=False), now=90)
    history.observe(reading(CHARGING, 117), now=100)
    history.observe(reading(IDLE, 120), now=110)
    assert history.latest is None


@pytest.mark.parametrize(("unknown_polls", "kept"), [(1, True), (2, False)])
def test_slow_scan_bridges_one_unknown_poll_but_not_two(unknown_polls, kept):
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=0)
    history.observe(reading(CONNECTED, 100), now=300)
    history.observe(reading(CHARGING, 101), now=600)
    now = 600
    for _ in range(unknown_polls):
        now += 300
        history.observe(None, now=now)
    history.observe(reading(CHARGING, 106), now=now + 300)
    history.observe(reading(IDLE, 110), now=now + 600)
    assert (history.latest is not None) is kept


def test_clock_rollback_restarts_the_unknown_span():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=1000)
    history.observe(reading(IDLE, 100), now=400)  # NTP correction
    history.observe(reading(IDLE, 100), now=405)  # fresh idle anchor on the new clock
    history.observe(reading(CONNECTED, 100), now=410)
    history.observe(reading(CHARGING, 101), now=420)
    for now in (450, 500, 560):
        history.observe(None, now=now)
    history.observe(reading(CHARGING, 106), now=570)
    history.observe(reading(IDLE, 110), now=580)
    assert history.latest is None


def test_clock_rollback_keeps_a_short_gap_bridgeable():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=1000)
    history.observe(reading(IDLE, 100), now=400)
    history.observe(reading(IDLE, 100), now=405)
    history.observe(reading(CONNECTED, 100), now=410)
    history.observe(reading(CHARGING, 101), now=420)
    history.observe(None, now=450)
    history.observe(reading(CHARGING, 106), now=470)
    history.observe(reading(IDLE, 110), now=480)
    assert history.latest.wallbox_kwh == 10


def test_energy_during_unknown_presence_still_hides_an_older_latest():
    history = complete()
    history.observe(reading(EvseState.NOT_INITIALIZED, 112), now=70)
    history.observe(reading(EvseState.NOT_INITIALIZED, 118), now=80)
    assert history.observe(reading(IDLE, 118), now=90)
    assert history.latest is None


def test_unplug_transition_read_then_final_idle_read_keeps_the_session():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=0)
    history.observe(reading(CHARGING, 101), now=10)
    history.observe(reading(CHARGING, 110, cp=CpState.A1), now=20)  # CP gone, EVSE lags; meter read
    assert not history.observe(reading(IDLE, 110, fresh=False), now=25)
    assert history.observe(reading(IDLE, 110), now=85)  # next slow meter read
    assert history.latest.wallbox_kwh == 10
    assert not history.observe(reading(IDLE, 110), now=145)
    assert history.latest.wallbox_kwh == 10


def test_clock_rollback_followed_by_failed_polls_still_expires():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=1000)
    history.observe(reading(IDLE, 100), now=400)
    history.observe(reading(IDLE, 100), now=405)
    history.observe(reading(CHARGING, 101), now=410)
    history.observe(None, now=300)  # The clock moves back again, then polls fail.
    for now in (360, 420, 480):
        history.observe(None, now=now)
    history.observe(reading(CHARGING, 106), now=490)
    history.observe(reading(IDLE, 110), now=500)
    assert history.latest is None


def test_a_silent_stall_between_known_polls_ends_the_cycle():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=0)
    history.observe(reading(CONNECTED, 100), now=10)
    history.observe(reading(CHARGING, 101), now=20)
    history.observe(reading(CHARGING, 105), now=500)  # host suspended
    history.observe(reading(IDLE, 110), now=510)
    assert history.latest is None


def test_a_bridged_stall_does_not_widen_the_next_allowance():
    history = SessionHistory()
    history.observe(reading(IDLE, 100), now=0)
    history.observe(reading(CONNECTED, 100), now=5)
    history.observe(reading(CHARGING, 101), now=10)
    history.observe(reading(CHARGING, 102), now=15)
    history.observe(reading(CHARGING, 103), now=125)  # bridged 110 s stall
    history.observe(reading(CHARGING, 105), now=395)  # 270 s stall
    history.observe(reading(IDLE, 110), now=400)
    assert history.latest is None


def test_replug_while_an_idle_plug_in_awaits_its_meter_keeps_the_last_charge():
    history = complete()
    previous = history.latest
    history.observe(reading(CONNECTED, 110), now=70)
    history.observe(reading(IDLE, 110, fresh=False), now=80)
    history.observe(reading(CONNECTED, 110), now=90)
    assert history.latest == previous
