"""A fresh vehicle match grants only the currently observed physical session."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu import plug_charge as m
from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A, B


class Coordinator:
    last_update_success = True

    def __init__(self):
        self.values = {"lock_evse": 0, "error_code": 0, "authorization_enabled": 0}
        self.listeners = []

    def current_data(self):
        return SimpleNamespace(values=self.values)

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def async_update_listeners(self):
        for listener in tuple(self.listeners):
            listener()


@asynccontextmanager
async def observer_harness(tmp_path, monkeypatch, *, selected=A, permitted=True):
    hass = HomeAssistant(str(tmp_path))
    options = detection_catalog()
    options["vehicle_auto_selection"] = False
    options["virtual_plug_charge"] = True
    options["active_vehicle_profile"] = selected
    options["vehicle_profiles"][A]["options"]["vehicle_plug_charge"] = permitted
    coordinator, timers = Coordinator(), []
    control = SimpleNamespace(
        read_only=False, pause_requested=False, charging_release_requested=None,
        energy_goal_stop_requested=False,
        plug_charge={"enabled": True, "policy": m.plug_charge_policy(options),
                     "session": "first-session", "authorized": False},
    )

    async def authorize(session, *, deadline):
        assert session == control.plug_charge["session"]
        assert 0 < (deadline - dt_util.utcnow()).total_seconds() <= 10
        control.plug_charge["authorized"] = True
        coordinator.async_update_listeners()

    control.async_authorize_plug_charge = AsyncMock(side_effect=authorize)
    entry = SimpleNamespace(options=options, runtime_data=SimpleNamespace(
        control=control, coordinator=coordinator,
    ))

    def schedule(_hass, seconds, callback):
        assert seconds == m.SETTLE_SECONDS
        timer = SimpleNamespace(callback=callback, cancelled=False)
        timers.append(timer)
        return lambda: setattr(timer, "cancelled", True)

    monkeypatch.setattr(m, "async_call_later", schedule)
    for name, plugged in (("first", "on"), ("second", "off")):
        hass.states.async_set(f"binary_sensor.{name}_plug", plugged)
        hass.states.async_set(f"device_tracker.{name}", "home")
    control.plug_charge["since"] = dt_util.utcnow().isoformat()
    observer = m.PlugChargeObserver(hass, entry)
    observer.start()
    harness = SimpleNamespace(hass=hass, entry=entry, control=control,
                              coordinator=coordinator, observer=observer, timers=timers)
    try:
        yield harness
    finally:
        await harness.observer.async_stop()
        assert not coordinator.listeners
        await hass.async_stop()


async def report(h, name="first", value="on"):
    h.hass.states.async_set(f"binary_sensor.{name}_plug", value)
    await h.hass.async_block_till_done()


def test_cached_match_waits_for_a_post_connection_report_and_never_resumes(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            assert h.observer.status == "waiting_detection"
            assert not h.timers
            h.control.async_authorize_plug_charge.assert_not_awaited()
            await report(h)
            assert h.observer.status == "waiting_release"
            assert len(h.timers) == 1
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_awaited_once()
            assert h.observer.status == "authorized"
            assert h.control.pause_requested is False
            assert h.control.charging_release_requested is None
            assert h.entry.options["vehicle_auto_selection"] is False
    asyncio.run(run())


def test_vehicle_report_between_last_absent_poll_and_first_connected_poll_is_accepted(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            report_at = h.hass.states.get("binary_sensor.first_plug").last_reported
            h.control.plug_charge["since"] = (report_at - timedelta(seconds=2)).isoformat()
            h.coordinator.async_update_listeners()
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("since", [None, "invalid", "2026-01-01T00:00:00", "2999-01-01T00:00:00+00:00"])
def test_missing_invalid_or_later_cycle_bound_cannot_accept_an_old_report(tmp_path, monkeypatch, since):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.control.plug_charge["since"] = since
            await report(h)
            assert h.observer.status == "waiting_detection"
            assert not h.timers
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("second", ["on", "unknown", "unavailable"])
def test_ambiguous_or_unknown_other_vehicle_never_grants(tmp_path, monkeypatch, second):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h, "second", second)
            await report(h)
            assert h.observer.status == "waiting_detection"
            assert not h.timers
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("selected", "permitted"), [(B, True), (None, True), (A, False)])
def test_manual_selection_and_profile_permission_are_not_detection(tmp_path, monkeypatch, selected, permitted):
    async def run():
        async with observer_harness(tmp_path, monkeypatch, selected=selected, permitted=permitted) as h:
            await report(h)
            assert h.observer.status == "vehicle_not_permitted"
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("owner", "key", "value"), [
    ("control", "read_only", True), ("control", "pause_requested", True),
    ("control", "charging_release_requested", False),
    ("control", "energy_goal_stop_requested", True),
    ("values", "lock_evse", 1), ("values", "lock_evse", None),
    ("values", "error_code", 200), ("values", "error_code", None),
    ("values", "authorization_enabled", 1),
    ("values", "authorization_enabled", None),
    ("coordinator", "last_update_success", False),
])
def test_existing_pause_lock_goals_rfid_and_unknown_state_block_release(tmp_path, monkeypatch, owner, key, value):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            if owner == "values":
                h.coordinator.values[key] = value
            else:
                setattr(getattr(h, owner), key, value)
            await report(h)
            assert h.observer.status == "blocked"
            assert not h.timers
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


def test_cancelled_old_timer_cannot_authorize_replacement_session_early(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h)
            old = h.timers[-1]
            h.control.plug_charge["session"] = "second-session"
            h.control.plug_charge["since"] = dt_util.utcnow().isoformat()
            h.coordinator.async_update_listeners()
            assert old.cancelled
            assert h.observer.status == "waiting_detection"
            await report(h)
            current = h.timers[-1]
            assert current is not old
            old.callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_not_awaited()
            current.callback(None)
            await h.hass.async_block_till_done()
            assert h.control.async_authorize_plug_charge.await_args.args == ("second-session",)
    asyncio.run(run())


def test_granted_session_survives_missing_cloud_data_and_ha_observer_restart(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.control.plug_charge["authorized"] = True
            h.entry.options["active_vehicle_profile"] = B
            h.coordinator.async_update_listeners()
            assert h.observer.status == "authorized"
            await report(h, value="unavailable")
            h.entry.options["active_vehicle_profile"] = None
            h.coordinator.async_update_listeners()
            assert h.observer.status == "authorized"
            await h.observer.async_stop()
            h.observer = m.PlugChargeObserver(h.hass, h.entry)
            h.observer.start()
            assert h.observer.status == "authorized"
            h.control.async_authorize_plug_charge.assert_not_awaited()
            h.control.plug_charge.update(session=None, since=None, authorized=False)
            h.coordinator.async_update_listeners()
            assert h.observer.status == "waiting_vehicle"
    asyncio.run(run())


@pytest.mark.parametrize("change", ["permission", "source", "policy", "disabled"])
def test_old_observer_cannot_grant_after_configuration_change(tmp_path, monkeypatch, change):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h)
            timer = h.timers[-1]
            if change == "permission":
                h.entry.options["vehicle_profiles"][A]["options"]["vehicle_plug_charge"] = False
            elif change == "source":
                h.entry.options["vehicle_profiles"][A]["detection"]["connected_entity"] = "binary_sensor.other"
            elif change == "policy":
                h.control.plug_charge["policy"] = "new-policy"
            else:
                h.entry.options["virtual_plug_charge"] = False
            timer.callback(None)
            await h.hass.async_block_till_done()
            assert h.observer.status == "disabled"
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


def test_config_change_before_deferred_authorization_executes_prevents_request(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h)
            pending = h.observer._authorize((A, "first-session"))
            h.entry.options["virtual_plug_charge"] = False
            await pending
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("finish", ["unload", "source_changed", "confirmed_grant"])
def test_inflight_authorization_cancels_on_unload_or_source_change_but_not_own_ack(tmp_path, monkeypatch, finish):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            started, cancelled, done = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def waiting(_session, *, deadline):
                started.set()
                try:
                    await done.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            h.control.async_authorize_plug_charge.side_effect = waiting
            await report(h)
            h.timers[-1].callback(None)
            await started.wait()
            if finish == "unload":
                await h.observer.async_stop()
            elif finish == "source_changed":
                h.entry.options["vehicle_profiles"][A]["detection"]["connected_entity"] = "binary_sensor.other"
                h.coordinator.async_update_listeners()
            else:
                h.control.plug_charge["authorized"] = True
                h.coordinator.async_update_listeners()
                await asyncio.sleep(0)
                assert not cancelled.is_set()
                done.set()
            await h.hass.async_block_till_done()
            assert cancelled.is_set() is (finish != "confirmed_grant")
    asyncio.run(run())


def test_failed_release_waits_for_retry_window_without_forcing_polls(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.control.async_authorize_plug_charge.side_effect = DeviceValidationError("plug_charge_blocked")
            await report(h)
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            count = len(h.timers)
            h.coordinator.async_update_listeners()
            assert len(h.timers) == count
            h.control.async_authorize_plug_charge.assert_awaited_once()
            h.observer._retry_at = 0
            h.coordinator.async_update_listeners()
            assert len(h.timers) == count + 1
    asyncio.run(run())


def test_policy_tracks_permissions_and_identity_without_binding_cosmetic_or_selection_changes():
    options = detection_catalog()
    original = m.plug_charge_policy(options)
    modified = deepcopy(options)
    modified["vehicle_profiles"][A]["name"] = "Renamed"
    modified["active_vehicle_profile"] = B
    modified["vehicle_profiles"][A]["options"]["timed_capacity_kwh"] = 80
    assert m.plug_charge_policy(modified) == original
    for key, value in (("provider", "bavariandata"), ("device_id", "other"), ("identity", "new")):
        changed = deepcopy(options)
        changed["vehicle_profiles"][A][key] = value
        if key == "provider":
            changed["vehicle_profiles"][A]["device_id"] = "car"
        assert m.plug_charge_policy(changed) != original
    modified["vehicle_profiles"][A]["options"]["vehicle_plug_charge"] = True
    assert m.plug_charge_policy(modified) != original
