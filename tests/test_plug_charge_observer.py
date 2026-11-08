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
async def observer_harness(tmp_path, monkeypatch, *, selected=A, plugged_in=True):
    hass = HomeAssistant(str(tmp_path))
    options = detection_catalog()
    options["vehicle_auto_selection"] = False
    options["virtual_plug_charge"] = True
    options["active_vehicle_profile"] = selected
    coordinator, timers = Coordinator(), []
    control = SimpleNamespace(
        read_only=False, pause_requested=False, charging_release_requested=None,
        energy_goal_stop_requested=False, pending_action=None, supports_soc_goal=True,
        soc_goal=SimpleNamespace(target_percent=0.0, stop_requested=False),
        async_resume_charging=AsyncMock(),
        plug_charge={"enabled": True, "policy": m.plug_charge_policy(options),
                     "session": None if plugged_in else "first-session", "authorized": False},
    )

    async def authorize(session, *, deadline):
        assert session == control.plug_charge["session"]
        assert 0 < (deadline - dt_util.utcnow()).total_seconds() <= 10
        control.plug_charge["authorized"] = True
        coordinator.async_update_listeners()

    control.async_authorize_plug_charge = AsyncMock(side_effect=authorize)
    entry = SimpleNamespace(options=options, runtime_data=SimpleNamespace(
        control=control, coordinator=coordinator, timed_goal=None, vehicle_soc=None,
        vehicle_profile_id=selected,
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
    if plugged_in:
        # The owner reported no vehicle before this session started.
        control.plug_charge["session"] = "first-session"
        coordinator.async_update_listeners()
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
            assert h.observer.blocked_reason is None
            assert len(h.timers) == 1
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_awaited_once()
            assert h.observer.status == "authorized"
            assert h.control.pause_requested is False
            assert h.control.charging_release_requested is None
            assert h.entry.options["vehicle_auto_selection"] is False
    asyncio.run(run())


def test_no_grant_while_the_selected_car_is_still_being_switched_in(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            # The options already name the car, but its target and departure
            # plan have not been started yet.
            h.entry.runtime_data.vehicle_profile_id = None
            await report(h)
            assert h.observer.status == "waiting_detection"
            assert not h.timers
            h.entry.runtime_data.vehicle_profile_id = A
            h.coordinator.async_update_listeners()
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_awaited_once()
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


@pytest.mark.parametrize("selected", [B, None])
def test_manual_selection_of_another_car_is_not_detection(tmp_path, monkeypatch, selected):
    async def run():
        async with observer_harness(tmp_path, monkeypatch, selected=selected) as h:
            await report(h)
            assert h.observer.status == "vehicle_not_selected"
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("owner", "key", "value", "reason"), [
    ("control", "read_only", True, "read_only"),
    ("control", "charging_release_requested", False, "release_off"),
    ("control", "energy_goal_stop_requested", True, "energy_goal_stop"),
    ("values", "lock_evse", 1, "wallbox_locked"),
    ("values", "lock_evse", None, "register_unavailable"),
    ("values", "error_code", 200, "error_code"),
    ("values", "error_code", None, "register_unavailable"),
    ("values", "authorization_enabled", 1, "authorization_required"),
    ("values", "authorization_enabled", None, "register_unavailable"),
    ("coordinator", "last_update_success", False, "connection_lost"),
])
def test_existing_pause_lock_goals_rfid_and_unknown_state_block_release(tmp_path, monkeypatch, owner, key, value, reason):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            if owner == "values":
                h.coordinator.values[key] = value
            else:
                setattr(getattr(h, owner), key, value)
            await report(h)
            assert h.observer.status == "blocked"
            assert h.observer.blocked_reason == reason
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


@pytest.mark.parametrize("change", ["source", "policy", "disabled"])
def test_old_observer_cannot_grant_after_configuration_change(tmp_path, monkeypatch, change):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await report(h)
            timer = h.timers[-1]
            if change == "source":
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


@pytest.mark.parametrize("failure", [DeviceValidationError("plug_charge_blocked"), OSError("transport unavailable")])
def test_failed_release_waits_for_retry_window_without_forcing_polls(tmp_path, monkeypatch, failure):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.control.async_authorize_plug_charge.side_effect = failure
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
    # A stored former per-profile permission no longer changes the policy.
    modified["vehicle_profiles"][A]["options"]["vehicle_plug_charge"] = True
    assert m.plug_charge_policy(modified) == original


def arm_plan(h, status):
    h.entry.runtime_data.timed_goal = SimpleNamespace(status=status, goal=SimpleNamespace(
        deadline=dt_util.utcnow() + timedelta(hours=8), finished=None,
    ))


async def plug_in_paused(h, *, pending=None, plan=None, goal_stop=False, paused=True):
    h.control.pause_requested = paused
    h.control.pending_action = pending
    h.control.energy_goal_stop_requested = goal_stop
    if plan is not None:
        arm_plan(h, plan)

    async def resume():
        h.control.pause_requested = False
        h.control.pending_action = None

    h.control.async_resume_charging.side_effect = resume
    await report(h)


def test_recognized_plug_in_ends_a_manual_pause_before_the_grant(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            order = []
            h.control.async_resume_charging.side_effect = None
            await plug_in_paused(h)
            assert h.observer.status == "waiting_release"
            assert h.observer.blocked_reason is None
            h.control.async_resume_charging.side_effect = lambda: order.append("resume")
            h.control.async_authorize_plug_charge.side_effect = (
                lambda session, *, deadline: order.append("authorize")
            )
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            assert order == ["resume", "authorize"]
    asyncio.run(run())


def test_a_finished_departure_plan_does_not_keep_the_pause(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            h.entry.runtime_data.timed_goal = SimpleNamespace(status="reached", goal=SimpleNamespace(
                deadline=dt_util.utcnow(), finished="reached",
            ))
            await plug_in_paused(h)
            assert h.observer.status == "waiting_release"
    asyncio.run(run())


@pytest.mark.parametrize(("change", "reason"), [
    ({"plan": "waiting_for_device"}, "departure_plan"),
    ({"plan": "queued"}, "departure_plan"),
    ({"plan": "paced"}, "departure_plan"),
    ({"plan": "paused_until_start", "paused": False}, "departure_plan"),
    ({"pending": "pause"}, "pause_requested"),
    ({"pending": "set_current"}, "pause_requested"),
    ({"goal_stop": True}, "energy_goal_stop"),
])
def test_automatic_or_newer_pauses_keep_blocking(tmp_path, monkeypatch, change, reason):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h, **change)
            assert h.observer.status == "blocked"
            assert h.observer.blocked_reason == reason
            assert not h.timers
            h.control.async_resume_charging.assert_not_awaited()
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


def test_a_queued_resume_is_awaited_instead_of_repeated(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h, pending="resume")
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_resume_charging.assert_not_awaited()
            h.control.async_authorize_plug_charge.assert_awaited_once()
    asyncio.run(run())


def test_a_failed_resume_defers_the_grant(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h)
            h.control.async_resume_charging.side_effect = DeviceValidationError("write_rate_limited")
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_resume_charging.assert_awaited_once()
            h.control.async_authorize_plug_charge.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("plan", "paused"), [("paused_until_start", True), ("paced", False), ("late", False)])
def test_an_applied_departure_plan_keeps_the_current_and_is_released(tmp_path, monkeypatch, plan, paused):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h, plan=plan, paused=paused)
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_resume_charging.assert_not_awaited()
            h.control.async_authorize_plug_charge.assert_awaited_once()
            assert h.control.pause_requested is paused
    asyncio.run(run())


@pytest.mark.parametrize("plan", ["waiting_for_soc", "waiting_for_target", "waiting_for_capacity"])
def test_a_plan_without_inputs_does_not_hold_the_manual_pause(tmp_path, monkeypatch, plan):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h, plan=plan)
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_resume_charging.assert_awaited_once()
            h.control.async_authorize_plug_charge.assert_awaited_once()
    asyncio.run(run())


def vehicle_limit(h, limit):
    observer = SimpleNamespace(maximum=limit, async_set_target=AsyncMock())
    h.entry.runtime_data.vehicle_soc = observer
    return observer


def test_each_recognized_connection_starts_at_the_car_limit(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            observer = vehicle_limit(h, 80)
            await report(h)
            observer.async_set_target.assert_awaited_once_with(80)
            h.coordinator.async_update_listeners()
            await h.hass.async_block_till_done()
            observer.async_set_target.assert_awaited_once()
            h.control.plug_charge["session"] = None
            h.coordinator.async_update_listeners()
            h.control.plug_charge["session"] = "second-session"
            h.coordinator.async_update_listeners()
            await h.hass.async_block_till_done()
            assert observer.async_set_target.await_count == 2
    asyncio.run(run())


@pytest.mark.parametrize(("limit", "target", "reached", "read_only", "adopted"), [
    (None, 0.0, False, False, False),
    (80, 80.0, False, False, False),
    (80, 80.0, True, False, True),
    (80, 0.0, False, True, False),
])
def test_car_limit_adoption_skips_missing_unchanged_or_read_only(
    tmp_path, monkeypatch, limit, target, reached, read_only, adopted,
):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            observer = vehicle_limit(h, limit)
            h.control.soc_goal = SimpleNamespace(target_percent=target, stop_requested=reached)
            h.control.read_only = read_only
            await report(h)
            assert observer.async_set_target.await_count == int(adopted)
    asyncio.run(run())


def test_a_refused_car_limit_does_not_affect_the_grant(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            observer = vehicle_limit(h, 80)
            observer.async_set_target.side_effect = DeviceValidationError("soc_goal_invalid")
            await report(h)
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_awaited_once()
    asyncio.run(run())


def test_a_restored_session_keeps_the_pause_and_the_target(tmp_path, monkeypatch):
    """A restart or reconnect re-creates the session of a car already plugged in."""
    async def run():
        async with observer_harness(tmp_path, monkeypatch, plugged_in=False) as h:
            observer = vehicle_limit(h, 80)
            h.control.soc_goal = SimpleNamespace(target_percent=60.0, stop_requested=True)
            await plug_in_paused(h)
            assert h.observer.status == "waiting_release"
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_resume_charging.assert_not_awaited()
            observer.async_set_target.assert_not_awaited()
            h.control.async_authorize_plug_charge.assert_awaited_once()
            assert h.control.pause_requested is True
    asyncio.run(run())


def test_a_session_replaced_without_an_unplug_is_not_a_new_plug_in(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            observer = vehicle_limit(h, 80)
            await report(h)
            observer.async_set_target.assert_awaited_once()
            h.control.plug_charge["session"] = "second-session"
            await plug_in_paused(h)
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            observer.async_set_target.assert_awaited_once()
            h.control.async_resume_charging.assert_not_awaited()
    asyncio.run(run())
