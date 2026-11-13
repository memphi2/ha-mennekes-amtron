"""Decision explanations are additive, private and have no charging side effects."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from custom_components.mennekes_amtron_rtu.diagnostics import _plug_charge_diagnostics
from tests.test_plug_charge_observer import observer_harness, report


@pytest.mark.parametrize(("second", "reason"), [
    ("on", "multiple_vehicles_match"),
    ("unknown", "vehicle_evidence_unknown"),
    ("unavailable", "vehicle_evidence_unknown"),
])
def test_unknown_and_ambiguous_cars_explain_waiting_without_granting(tmp_path, monkeypatch, second, reason):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h, "second", second)
            await report(h)
            assert h.observer.status == "waiting_detection"
            assert h.observer.decision_reason == reason
            assert h.observer.blocked_reason is None
            before = h.control.plug_charge.copy()
            details = h.observer.decision_details
            # The support view names the same reason, for the car evidence too.
            assert details["reason"] == reason
            assert details["detection"]["reason"] == reason
            assert details["last_grant_error"] is None
            # Reading it changes neither the gate nor the decision.
            assert h.control.plug_charge == before
            assert h.observer.decision_reason == reason
            h.control.async_authorize_plug_charge.assert_not_awaited()
            h.control.async_resume_charging.assert_not_awaited()
    asyncio.run(run())


def test_report_binding_and_settling_are_distinct_waits(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.control.plug_charge["since"] = (dt_util.utcnow() + timedelta(minutes=3)).isoformat()
            await report(h)
            assert h.observer.decision_reason == "plug_report_not_current"
            h.control.plug_charge["since"] = None
            h.coordinator.async_update_listeners()
            assert h.observer.decision_reason == "session_time_unknown"
            h.control.plug_charge["since"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
            profile = h.entry.runtime_data.vehicle_profile_id
            h.entry.runtime_data.vehicle_profile_id = None
            h.coordinator.async_update_listeners()
            assert h.observer.decision_reason == "vehicle_binding_pending"
            h.entry.runtime_data.vehicle_profile_id = profile
            h.coordinator.async_update_listeners()
            assert h.observer.decision_reason == "settling"
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


def test_all_blockers_are_visible_and_the_legacy_first_reason_is_unchanged(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.control.read_only = True
            h.control.charging_release_requested = False
            h.control.energy_goal_stop_requested = True
            h.coordinator.values.update(lock_evse=1, error_code=200, authorization_enabled=None)
            await report(h)
            assert h.observer.blocked_reason == "read_only"
            details = h.observer.decision_details
            assert details["release_blockers"] == [
                "read_only", "release_off", "energy_goal_stop", "wallbox_locked", "error_code", "register_unavailable",
            ]
            assert details["unavailable_registers"] == ["authorization_enabled"]
            # The export contains the explanation, never tokens, hashes or IDs.
            runtime = SimpleNamespace(plug_charge=h.observer, control=h.control)
            dump = json.dumps(_plug_charge_diagnostics(runtime))
            assert '"decision"' in dump
            for secret in (h.control.plug_charge["session"], h.control.plug_charge["policy"], h.entry.runtime_data.vehicle_profile_id):
                assert secret not in dump
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("error", "category"), [
    (DeviceValidationError("write_rate_limited"), "write_rate_limited"),
    (OSError("private-adapter-address"), "command_failed"),
])
def test_failed_grant_is_explained_without_exposing_raw_errors(tmp_path, monkeypatch, error, category):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h)
            h.control.async_authorize_plug_charge.side_effect = error
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            assert h.observer.status == "waiting_release"
            assert h.observer.decision_reason == "grant_retry"
            assert h.observer.decision_details["last_grant_error"] == category
            assert "private-adapter-address" not in json.dumps(h.observer.decision_details)
            h.control.plug_charge["authorized"] = True
            h.coordinator.async_update_listeners()
            assert h.observer.decision_reason == "authorized"
            assert h.observer.decision_details["last_grant_error"] is None
    asyncio.run(run())
