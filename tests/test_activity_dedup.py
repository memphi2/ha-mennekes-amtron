"""Only identical successful setpoints are quiet in the readable HA timeline."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.event import (
    AmtronControlResultEvent,
    AmtronWallboxEvent,
)
from tests.ha_fakes import build_runtime


async def _setup(monkeypatch):
    hass, entry, _transport = await build_runtime()
    wallbox, raw = AmtronWallboxEvent(entry), AmtronControlResultEvent(entry)
    wallbox.hass = raw.hass = hass
    wallbox.entity_id = "event.wallbox"
    rows, raw_rows = [], []
    monkeypatch.setattr(wallbox, "async_write_ha_state", lambda: None)
    monkeypatch.setattr(raw, "async_write_ha_state", lambda: raw_rows.append(raw.state_attributes.copy()))
    monkeypatch.setattr("custom_components.mennekes_amtron_rtu.event.async_log_entry",
                        lambda *args, **_kwargs: rows.append(args[2]))
    feed = entry.runtime_data.control.events
    feed.listen(wallbox._log_activity)
    feed.listen(raw._handle_event)
    return entry, wallbox, feed, rows, raw_rows


def test_repeated_successful_setpoints_keep_full_raw_feed_but_one_activity(monkeypatch):
    async def run():
        _entry, _wallbox, feed, rows, raw_rows = await _setup(monkeypatch)
        for _ in range(5):
            feed.record("executed", "set_current", source="home_assistant", requested_value=8)
        assert len(rows) == 1
        assert len(raw_rows) == 5
        assert len(feed.snapshot()["events"]) == 5
        for source, value, reason in [
            ("departure_plan", 8, "completed"),
            ("departure_plan", 9, "completed"),
            ("departure_plan", 9, "vehicle_authorized"),
            ("departure_plan", 0, "completed"),
        ]:
            feed.record("executed", "set_current", reason, source=source, requested_value=value)
        assert len(rows) == 5
        assert len(raw_rows) == 9
    asyncio.run(run())


@pytest.mark.parametrize(("action", "outcome", "reason", "visible"), [
    ("set_current", "failed", "connection_error", True),
    ("set_current", "uncertain", "connection_error", True),
    ("set_current", "executed", "status_refresh_failed", True),
    ("set_current", "executed", "unknown", True),
    ("set_current", "cancelled", "user_cancelled", True),
    ("set_current", "replaced", "replaced", False),
    ("pause", "executed", "completed", True),
])
def test_intervening_actions_and_failures_reset_success_comparison(monkeypatch, action, outcome, reason, visible):
    async def run():
        _entry, _wallbox, feed, rows, _raw = await _setup(monkeypatch)
        feed.record("executed", "set_current", requested_value=8)
        for _ in range(2):
            feed.record(outcome, action, reason, requested_value=8)
        assert len(rows) == (3 if visible else 1)
        feed.record("executed", "set_current", requested_value=8)
        assert len(rows) == (4 if visible else 2)
    asyncio.run(run())


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, 33])
def test_unknown_or_invalid_requested_values_are_not_silenced(monkeypatch, value):
    async def run():
        _entry, _wallbox, feed, rows, _raw = await _setup(monkeypatch)
        for _ in range(2):
            feed.record("executed", "set_current", requested_value=value)
        assert len(rows) == 2
    asyncio.run(run())


def test_confirming_readbacks_are_quiet_but_external_current_changes_reset(monkeypatch):
    async def run():
        entry, wallbox, feed, rows, _raw = await _setup(monkeypatch)
        data = entry.runtime_data.coordinator.data
        data.values["charging_current_ems"] = 6
        wallbox._handle_coordinator_update()
        feed.record("executed", "set_current", requested_value=8.1)
        # A cached pre-write value does not pretend the current changed again.
        wallbox._handle_coordinator_update()
        feed.record("executed", "set_current", requested_value=8.1)
        data.values["charging_current_ems"] = 8.100000381469727
        wallbox._handle_coordinator_update()
        feed.record("executed", "set_current", requested_value=8.1)
        assert len(rows) == 1
        data.values["charging_current_ems"] = 6
        wallbox._handle_coordinator_update()
        feed.record("executed", "set_current", requested_value=8.1)
        assert len(rows) == 2
    asyncio.run(run())


@pytest.mark.parametrize("change", ["disconnect", "held", "failed_block", "missing", "physical"])
def test_reading_gaps_and_physical_changes_allow_a_new_same_value_result(monkeypatch, change):
    async def run():
        entry, wallbox, feed, rows, _raw = await _setup(monkeypatch)
        coordinator = entry.runtime_data.coordinator
        coordinator.data = WallboxData(values={
            "evse_state": EvseState.EV_CONNECTED, "cp_state": CpState.B1,
            "error_code": 0, "charging_current_ems": 8,
        })
        wallbox._handle_coordinator_update()
        feed.record("executed", "set_current", requested_value=8)
        if change == "disconnect":
            coordinator.last_update_success = False
        elif change == "held":
            coordinator.data.held = frozenset({"charging_current_ems"})
        elif change == "failed_block":
            # Remote snapshots retain cached values and failed-block metadata,
            # but do not carry the direct poller's held-key set.
            coordinator.data.failed_blocks = ("current_limits",)
        elif change == "missing":
            coordinator.data.values.pop("charging_current_ems")
        else:
            coordinator.data.values.update(evse_state=EvseState.IDLE, cp_state=CpState.A1)
        wallbox._handle_coordinator_update()
        feed.record("executed", "set_current", requested_value=8)
        assert len(rows) == 2
    asyncio.run(run())
