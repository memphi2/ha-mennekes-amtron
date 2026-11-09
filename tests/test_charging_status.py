"""Explain observed charging state without inventing a vehicle's intent."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.mennekes_amtron_rtu.device.charging_status import (
    CHARGING_STATUS_OPTIONS,
    charging_status,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.registers import LAYOUT_V01_00
from custom_components.mennekes_amtron_rtu.sensor import async_setup_entry
from tests.ha_fakes import build_runtime, collect


def _data(**values):
    return WallboxData(values={
        "evse_state": EvseState.EV_CONNECTED,
        "cp_state": CpState.B1,
        "error_code": 0,
        "lock_evse": 0,
        "charging_release": 1,
        "charging_current_ems": 16.0,
        "authorization_status": 1,
        **values,
    })


@pytest.mark.parametrize(("values", "expected"), [
    ({"evse_state": EvseState.NOT_INITIALIZED}, "initializing"),
    ({"evse_state": EvseState.ERROR}, "wallbox_error"),
    ({"error_code": 200}, "wallbox_error"),
    ({"error_code": 4711}, "wallbox_error"),
    ({"evse_state": EvseState.SERVICE_MODE}, "service_mode"),
    ({"evse_state": EvseState.IDLE, "cp_state": CpState.A1}, "no_vehicle"),
    ({"evse_state": EvseState.CHARGING, "cp_state": CpState.C2}, "charging"),
    ({"evse_state": EvseState.CHARGING, "cp_state": CpState.D2}, "charging"),
    ({"evse_state": EvseState.CHARGING, "cp_state": None}, "charging"),
    ({"lock_evse": 1}, "locked"),
    ({"charging_release": 0}, "release_disabled"),
    ({"charging_current_ems": 1.0}, "paused"),
    ({"authorization_status": 2}, "authorization_required"),
    ({}, "preparing"),
    ({"evse_state": EvseState.PRECONDITIONS_VALID}, "preparing"),
    ({"evse_state": EvseState.READY_TO_CHARGE, "cp_state": CpState.B2}, "waiting_for_vehicle"),
    ({"charging_current_ems": 0}, "preparing"),
    ({"authorization_status": 0}, "preparing"),
])
def test_status_reports_observed_states_and_zero_is_not_pause(values, expected):
    assert expected in CHARGING_STATUS_OPTIONS
    assert charging_status(_data(**values), cp_supported=True) == expected


@pytest.mark.parametrize(("values", "expected"), [
    ({"error_code": 200, "lock_evse": 1}, "wallbox_error"),
    ({"evse_state": EvseState.SERVICE_MODE, "lock_evse": 1}, "service_mode"),
    ({"evse_state": EvseState.IDLE, "cp_state": CpState.A2, "lock_evse": 1}, "no_vehicle"),
    ({"lock_evse": 1, "charging_release": 0, "charging_current_ems": 1.0}, "locked"),
    ({"charging_release": 0, "charging_current_ems": 1.0, "authorization_status": 2}, "release_disabled"),
    ({"charging_current_ems": 1.0, "authorization_status": 2}, "paused"),
    ({"lock_evse": 1, "charging_release": None}, "locked"),
])
def test_multiple_conditions_use_stable_documented_precedence(values, expected):
    assert charging_status(_data(**values), cp_supported=True) == expected


@pytest.mark.parametrize("values", [
    {"evse_state": None}, {"evse_state": 99}, {"evse_state": True},
    {"evse_state": 2.0}, {"evse_state": -1},
    {"evse_state": EvseState.IDLE},
    {"cp_state": CpState.A1},
    {"cp_state": CpState.INIT}, {"cp_state": CpState.E}, {"cp_state": CpState.F},
    {"lock_evse": None}, {"lock_evse": 2}, {"lock_evse": True},
    {"charging_release": None}, {"charging_release": 2},
    {"authorization_status": None}, {"authorization_status": 3},
    {"error_code": None}, {"error_code": True}, {"error_code": -1},
    {"charging_current_ems": None}, {"charging_current_ems": True},
    {"charging_current_ems": -1}, {"charging_current_ems": float("nan")},
    {"charging_current_ems": float("inf")},
])
def test_missing_or_conflicting_prerequisites_do_not_blame_the_vehicle(values):
    assert charging_status(_data(**values), cp_supported=True) is None


@pytest.mark.parametrize("contradiction", [
    {"lock_evse": 1}, {"charging_release": 0},
    {"charging_current_ems": 1.0}, {"authorization_status": 2},
    {"cp_state": CpState.B1}, {"cp_state": CpState.E}, {"cp_state": True},
])
def test_contradictory_charging_snapshot_is_unknown(contradiction):
    values = {"evse_state": EvseState.CHARGING, "cp_state": CpState.C2, **contradiction}
    assert charging_status(_data(**values), cp_supported=True) is None


def test_old_layout_uses_its_evse_state_without_a_pilot_register():
    assert charging_status(_data(evse_state=EvseState.IDLE), cp_supported=False) == "no_vehicle"
    assert charging_status(_data(evse_state=EvseState.CHARGING), cp_supported=False) == "charging"


def test_pending_intent_and_measurement_fluctuations_do_not_change_observed_state():
    data = _data(charging_current_ems=1.0)
    data.pending_change = "resume"
    data.pending_change_until = datetime(2026, 11, 6, tzinfo=UTC)
    assert charging_status(data, cp_supported=True) == "paused"
    data.values.update(power_total=0.3, voltage_l1=230.01, session_duration=61)
    assert charging_status(data, cp_supported=True) == "paused"
    assert not {"full", "completed", "vehicle_refused"}.intersection(CHARGING_STATUS_OPTIONS)


@pytest.mark.parametrize("layout", [None, LAYOUT_V01_00])
@pytest.mark.parametrize("read_only", [False, True])
def test_derived_entities_are_available_without_control_and_record_no_counters(layout, read_only):
    async def run():
        hass, entry, _transport = await build_runtime(
            read_only=read_only, **({"layout": layout} if layout else {}),
        )
        added = {}
        await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
        status = added["charging_status"]
        quality = added["connection_quality"]
        coordinator = entry.runtime_data.coordinator
        coordinator.data.values.update(evse_state=EvseState.READY_TO_CHARGE, cp_state=CpState.B2)
        assert status.available
        assert status.native_value == "waiting_for_vehicle"
        assert status.entity_category is None
        assert status.device_class is SensorDeviceClass.ENUM
        assert status.state_class is None
        assert status.extra_state_attributes is None
        assert status.unique_id.endswith("_charging_status")
        assert quality.entity_category is EntityCategory.DIAGNOSTIC
        assert quality.state_class is None
        assert quality.extra_state_attributes == {"failed_transactions": 0, "retried_transactions": 0}
        assert quality.unique_id.endswith("_connection_quality")
        for value in (None, "good", "degraded", "good"):
            coordinator.data.connection_quality = value
            assert quality.native_value == value
        coordinator.data.connection_failures = 4
        assert quality.extra_state_attributes == {"failed_transactions": 4, "retried_transactions": 0}
        coordinator.data.connection_retries = None
        assert quality.extra_state_attributes == {"failed_transactions": 4}
        coordinator.data.connection_failures = None
        assert quality.extra_state_attributes is None
        coordinator.last_update_success = False
        assert not status.available
        assert not quality.available
        coordinator.last_update_success = True
        coordinator.data.values.clear()
        assert status.available
        assert status.native_value is None

    asyncio.run(run())
