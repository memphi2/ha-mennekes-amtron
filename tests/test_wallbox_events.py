"""Wallbox events report observed changes only, never a baseline or a replay."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.command_events import CommandEvent
from custom_components.mennekes_amtron_rtu.device.enums import (
    AuthorizationStatus,
    CableLockStatus,
    CpState,
    DowngradeStatus,
    EvseState,
    SwitchedPhases,
)
from custom_components.mennekes_amtron_rtu.device.wallbox_events import (
    EVENT_TYPES,
    WallboxChanges,
)
from custom_components.mennekes_amtron_rtu.event import (
    AmtronActivity,
    async_setup_entry,
)
from tests.fakes import put, raise_connection_error
from tests.ha_fakes import build_runtime

PLUGGED = {
    "evse_state": EvseState.EV_CONNECTED, "cp_state": CpState.B1, "error_code": 0,
    "authorization_status": AuthorizationStatus.NOT_USED,
    "downgrade_status": DowngradeStatus.NOT_DOWNGRADED,
    "cable_lock_status": CableLockStatus.UNLOCKED,
    "switched_phases": SwitchedPhases.ALL_AVAILABLE,
    "session_energy": 0.0, "session_duration": 0,
}


def snapshot(**changes: object) -> WallboxData:
    return WallboxData(values={**PLUGGED, **changes})


def types(events: list) -> list[str]:
    return [event.type for event in events]


def test_the_first_reading_is_a_baseline_and_a_repeat_reports_nothing() -> None:
    changes = WallboxChanges(cp_supported=True)
    assert changes.observe(snapshot()) == []
    assert changes.observe(snapshot()) == []
    assert changes.observe(WallboxData()) == []


def test_plugging_charging_and_unplugging_carry_the_session_counters() -> None:
    changes = WallboxChanges(cp_supported=True)
    changes.observe(snapshot(evse_state=EvseState.IDLE, cp_state=CpState.A1))
    [plugged] = changes.observe(snapshot())
    assert (plugged.type, plugged.attributes) == ("vehicle_plugged_in", {"cp_state": "b1"})
    [started] = changes.observe(snapshot(evse_state=EvseState.CHARGING, cp_state=CpState.C2))
    assert (started.type, started.attributes) == ("charging_started", {"evse_state": "charging"})
    changes.observe(snapshot(
        evse_state=EvseState.CHARGING, cp_state=CpState.C2, session_energy=7.5, session_duration=3600,
    ))
    # The wallbox resets the counters in the very poll that shows the car gone.
    stopped, unplugged = changes.observe(snapshot(
        evse_state=EvseState.IDLE, cp_state=CpState.A1, session_energy=0.0, session_duration=0,
    ))
    assert stopped.type == "charging_stopped"
    assert stopped.attributes == {
        "evse_state": "idle", "session_energy": 7.5, "session_duration": 3600.0,
    }
    assert unplugged.type == "vehicle_unplugged"
    assert unplugged.attributes == {
        "cp_state": "a1", "session_energy": 7.5, "session_duration": 3600.0,
    }


def test_an_unknown_reading_in_between_keeps_the_last_known_baseline() -> None:
    changes = WallboxChanges(cp_supported=True)
    changes.observe(snapshot())
    # An error state makes the presence unknown; the error itself is reported.
    assert types(changes.observe(snapshot(evse_state=EvseState.ERROR, error_code=2011))) == [
        "error_occurred",
    ]
    # The car is still there once the error clears: no plug-in is invented.
    [cleared] = changes.observe(snapshot())
    assert (cleared.type, cleared.attributes) == (
        "error_cleared", {"error_code": 2011, "error_category": "ev_overcurrent"},
    )
    changes.observe(snapshot(evse_state=EvseState.ERROR, error_code=2011))
    assert types(changes.observe(snapshot(evse_state=EvseState.IDLE, cp_state=CpState.A1))) == [
        "vehicle_unplugged", "error_cleared",
    ]


def test_a_failed_poll_resets_the_baseline_instead_of_replaying() -> None:
    changes = WallboxChanges(cp_supported=True)
    changes.observe(snapshot())
    assert changes.observe(None) == []
    assert changes.observe(snapshot(evse_state=EvseState.IDLE, cp_state=CpState.A1)) == []
    assert types(changes.observe(snapshot())) == ["vehicle_plugged_in"]


def test_authorization_downgrade_lock_and_phases_are_reported() -> None:
    changes = WallboxChanges(cp_supported=True)
    changes.observe(snapshot())
    [authorized] = changes.observe(snapshot(authorization_status=AuthorizationStatus.AUTHORIZED))
    assert (authorized.type, authorized.attributes) == ("authorized", {"authorization": "authorized"})
    [rejected] = changes.observe(snapshot(authorization_status=AuthorizationStatus.NOT_AUTHORIZED))
    assert rejected.type == "authorization_rejected"
    # Back to "not used" is no decision of the wallbox.
    assert changes.observe(snapshot()) == []
    [started] = changes.observe(snapshot(
        downgrade_status=DowngradeStatus.DOWNGRADED, downgrade_current=10.0,
    ))
    assert (started.type, started.attributes) == ("downgrade_started", {"downgrade_current": 10.0})
    [ended] = changes.observe(snapshot(downgrade_status=DowngradeStatus.NOT_RELEVANT))
    assert (ended.type, dict(ended.attributes)) == ("downgrade_ended", {})
    [locked] = changes.observe(snapshot(cable_lock_status=CableLockStatus.LOCKED))
    assert (locked.type, locked.attributes) == ("cable_locked", {"cable_lock": "locked"})
    # A fixed cable or an undetermined lock neither locks nor unlocks.
    assert changes.observe(snapshot(cable_lock_status=CableLockStatus.FIXED_CABLE)) == []
    [unlocked] = changes.observe(snapshot())
    assert unlocked.type == "cable_unlocked"
    [phases] = changes.observe(snapshot(switched_phases=SwitchedPhases.SINGLE_PHASE))
    assert (phases.type, phases.attributes) == ("phases_switched", {"phases": "single_phase"})


def test_invalid_values_are_neither_a_change_nor_a_baseline() -> None:
    changes = WallboxChanges(cp_supported=False)
    changes.observe(snapshot(cp_state=None, error_code="7", authorization_status=9, switched_phases=True))
    assert changes.observe(snapshot(cp_state=None, error_code=200)) == []
    [cleared] = changes.observe(snapshot(cp_state=None))
    assert cleared.attributes == {"error_code": 200, "error_category": "energy_manager_unavailable"}
    assert types(changes.observe(snapshot(cp_state=None, evse_state=EvseState.IDLE))) == [
        "vehicle_unplugged",
    ]
    assert all(event_type in EVENT_TYPES for event_type in (
        "vehicle_unplugged", "error_cleared", "phases_switched",
    ))


def test_the_entity_reports_changes_after_a_baseline_and_restarts_after_a_failed_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        put(transport.bank, R.EVSE_STATE, EvseState.IDLE)
        put(transport.bank, R.CP_STATE, CpState.A1)
        await entry.runtime_data.coordinator.async_refresh()
        added: list = []
        await async_setup_entry(hass, entry, added.extend)
        [entity] = added
        assert isinstance(entity, AmtronActivity)
        written: list[tuple[str | None, str | None]] = []
        monkeypatch.setattr(
            entity, "async_write_ha_state",
            lambda: written.append((entity.state_attributes["event_type"], entity.state)),
        )
        entity.hass = hass
        entity.entity_id = "event.test_activity"
        await entity.async_added_to_hass()
        assert entity.state is None

        async def command(*args, **kwargs) -> None:
            # Outcomes reach Home Assistant through the service's own feed.
            entry.service.control.events.record(*args, **kwargs)
            await entry.runtime_data.coordinator.async_refresh()

        await command("executed", "resume", source="plug_charge", requested_value=16)
        assert len(written) == 1
        assert entity.state_attributes["result"] == "executed"
        assert entity.state_attributes["trigger"] == "plug_charge"
        # Dragging the slider supersedes values; only the final outcome counts.
        await command("replaced", "set_current", "replaced", requested_value=10)
        assert len(written) == 1
        await command("executed", "set_current", requested_value=0)
        assert entity.state_attributes["value"] == 0
        await command("cancelled", "pause", "user_cancelled")
        assert entity.state_attributes["action"] == "pause"
        # The type alone says what happened: it is the whole logbook row.
        assert [kind for kind, _at in written] == ["resumed", "current_set", "command_cancelled"]
        written.clear()

        put(transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)
        put(transport.bank, R.CP_STATE, CpState.B1)
        await entry.runtime_data.coordinator.async_refresh()
        assert [kind for kind, _at in written] == ["vehicle_plugged_in"]
        assert entity.state_attributes["cp_state"] == "b1"
        plugged_at = entity.state

        put(transport.bank, R.EVSE_STATE, EvseState.CHARGING)
        put(transport.bank, R.CP_STATE, CpState.C2)
        put(transport.bank, R.ERROR_CODE, 2300)
        await entry.runtime_data.coordinator.async_refresh()
        assert [kind for kind, _at in written] == ["vehicle_plugged_in", "charging_started", "error_occurred"]
        assert entity.state > plugged_at
        states = {state for _kind, state in written}
        assert len(states) == 3

        # A lost connection resets the baseline: the car that left meanwhile
        # is not reported as unplugged at an invented time.
        transport.read_exceptions[R.EVSE_STATE.address] = raise_connection_error()
        await entry.runtime_data.coordinator.async_refresh()
        # Events are moments: the entity stays available without a reading.
        assert entity.available
        transport.read_exceptions.clear()
        put(transport.bank, R.EVSE_STATE, EvseState.IDLE)
        put(transport.bank, R.CP_STATE, CpState.A1)
        put(transport.bank, R.ERROR_CODE, 0)
        await entry.runtime_data.coordinator.async_refresh()
        assert {state for _kind, state in written} == states
        assert written[-1][0] == "error_occurred"
        put(transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)
        put(transport.bank, R.CP_STATE, CpState.B1)
        await entry.runtime_data.coordinator.async_refresh()
        assert written[-1][0] == "vehicle_plugged_in"

        # Removing the entity ends its subscription to the command feed.
        for remove in entity._on_remove or ():
            remove()
        await command("executed", "pause")
        assert written[-1][0] == "vehicle_plugged_in"
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_every_command_keeps_the_time_the_service_ran_it(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        added: list = []
        await async_setup_entry(hass, entry, added.extend)
        [entity] = added
        entity.hass = hass
        entity.entity_id = "event.test_activity"
        triggered: list = []
        monkeypatch.setattr(entity, "async_write_ha_state", lambda: triggered.append(entity.state_attributes))
        times = ("2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00", None)
        for sequence, occurred_at in enumerate(times, start=1):
            entity._command(CommandEvent(
                sequence, "pause", "executed", "completed", "energy_goal", occurred_at,
            ))
        # Three commands are three events, each with the service's own time.
        assert [attributes["event_type"] for attributes in triggered] == ["paused"] * 3
        assert [attributes.get("occurred_at") for attributes in triggered] == list(times)

    asyncio.run(run())
