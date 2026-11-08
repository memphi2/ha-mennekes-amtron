"""Delayed automatic recognition owns only its observed physical charge cycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu import loss_attribution as m
from custom_components.mennekes_amtron_rtu import vehicle_detection, vehicle_profiles
from custom_components.mennekes_amtron_rtu.charge_loss_observer import (
    ChargeLossObserver,
)
from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.test_charge_loss_observer import Coordinator
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A, B, catalog


def setup(hass, monkeypatch, *, automatic=True, selected=None, auto_match=False):
    now = dt_util.utcnow().timestamp()
    clock = [now - 120]
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(m, "time", fake_time)
    monkeypatch.setattr(vehicle_profiles, "time", fake_time)
    coordinator = Coordinator()
    options = detection_catalog() if automatic else catalog()
    options["active_vehicle_profile"] = selected
    options["vehicle_profile_selected_at"] = now - 200
    for profile in options["vehicle_profiles"].values():
        profile["options"]["charge_loss_auto_match"] = auto_match
    entry = SimpleNamespace(entry_id="entry", options=options, runtime_data=SimpleNamespace(
        coordinator=coordinator, identity=SimpleNamespace(serial_number="123", layout_version=0x0103),
        client=SimpleNamespace(connected=True),
    ))
    unsubscribe = m.track_loss_attribution(hass, entry)
    return coordinator, entry, clock, now, unsubscribe


def charging(coordinator):
    coordinator.data.values.update(evse_state=EvseState.CHARGING, cp_state=CpState.C2)
    coordinator.async_update_listeners()


def complete(coordinator, now, *, connected_offset=-110, start_offset=-100):
    record = CompletedSession(str(uuid4()), now + connected_offset, now,
                              now + start_offset, now + start_offset + 5, now - 10, now - 5, 10)
    coordinator.data.values.update(evse_state=EvseState.IDLE, cp_state=CpState.A1)
    coordinator.data.charging_session = record.as_dict()
    coordinator.async_update_listeners()
    return record


def choose(hass, entry, clock, profile, *, automatic=True):
    if profile is not None and automatic:
        m.confirm_automatic_vehicle(hass, entry, profile)
    entry.options = vehicle_profiles.activate_profile(entry.options, profile)
    if not automatic:
        entry.options["vehicle_auto_selection"] = False
    m.loss_vehicle_options(hass, entry)


@pytest.mark.parametrize("deselect_before_completion", [False, True])
@pytest.mark.parametrize("auto_match", [False, True])
@pytest.mark.parametrize("report_delay", [3, 180, 900])
def test_delayed_auto_selection_matches_its_completed_charge_after_reload(
    tmp_path, monkeypatch, deselect_before_completion, auto_match, report_delay,
):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch, auto_match=auto_match)
        hass.states.async_set("sensor.first_energy", "7", {"unit_of_measurement": "kWh"})
        observer = None
        try:
            charge_start = now - report_delay - 60
            clock[0] = charge_start
            charging(coordinator)
            # The car reports its cable after 3s, 3min or 15min, then settles.
            clock[0] = charge_start + report_delay + 3
            choose(hass, entry, clock, A)
            assert entry.options["vehicle_profile_selected_at"] > charge_start
            if deselect_before_completion:
                clock[0] = now - 1
                choose(hass, entry, clock, None)
                assert entry.options["active_vehicle_profile"] is None
                assert m.loss_vehicle_options(hass, entry)["vehicle_profile_id"] == A
            unsubscribe()
            unsubscribe = m.track_loss_attribution(hass, entry)
            observer = ChargeLossObserver(hass, entry)
            await observer.async_start()
            record = complete(
                coordinator, dt_util.utcnow().timestamp(),
                connected_offset=-report_delay - 70, start_offset=-report_delay - 60,
            )
            await hass.async_block_till_done()
            assert m.attributed_session(hass, entry, A, record) is True
            assert observer.can_assign
            hass.states.async_set("sensor.first_energy", "8", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            if not auto_match:
                await observer.async_assign()
            assert observer.statistics.sample_count == 1
            assert observer.statistics.last_loss_percent == pytest.approx(20)
            assert observer.statistics.average_loss_percent == pytest.approx(20)
        finally:
            if observer is not None:
                await observer.async_stop()
            unsubscribe()
            m.forget_loss_attribution(hass, entry.entry_id)
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("physical_first", [False, True])
def test_actual_detection_settling_records_current_cycle_provenance(tmp_path, monkeypatch, physical_first):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch)
        timers = []
        monkeypatch.setattr(hass.config_entries, "async_update_entry", lambda entry, *, options: setattr(entry, "options", options))
        monkeypatch.setattr(vehicle_detection, "async_call_later", lambda hass, seconds, callback: (timers.append(callback) or (lambda: None)))
        for name in ("first", "second"):
            hass.states.async_set(f"binary_sensor.{name}_plug", "off")
            hass.states.async_set(f"device_tracker.{name}", "home")
        detector = vehicle_detection.VehicleDetectionObserver(hass, entry)
        try:
            detector.start()
            if physical_first:
                charging(coordinator)
            hass.states.async_set("binary_sensor.first_plug", "on")
            await hass.async_block_till_done()
            assert len(timers) == 1
            clock[0] = now - 87
            timers.pop()(None)
            assert entry.options["active_vehicle_profile"] == A
            if not physical_first:
                # Selection may precede the first connected wallbox poll;
                # the unchanged selected car must gain proof on that poll.
                charging(coordinator)
            coordinator.async_update_listeners()
            record = complete(coordinator, now)
            assert m.attributed_session(hass, entry, A, record) is True
        finally:
            detector.stop()
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("conflict", [False, True])
def test_unplug_before_delayed_summary_keeps_only_uncontested_owner(tmp_path, monkeypatch, conflict):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch)
        try:
            assert "vehicle_profile_id" not in m.loss_vehicle_options(hass, entry)
            charging(coordinator)
            clock[0] = now - 87
            choose(hass, entry, clock, A)
            if conflict:
                choose(hass, entry, clock, B)
            coordinator.data.values.update(evse_state=EvseState.IDLE, cp_state=CpState.A1)
            coordinator.async_update_listeners()
            choose(hass, entry, clock, None)
            assert m.loss_vehicle_options(hass, entry).get("vehicle_profile_id") == (None if conflict else A)
            record = complete(coordinator, now)
            assert m.attributed_session(hass, entry, A, record) is (not conflict)
        finally:
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("selected_at_offset", [-200, -80, "invalid"])
def test_manual_selection_keeps_start_boundary_and_is_not_upgraded_to_auto(tmp_path, monkeypatch, selected_at_offset):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch, automatic=False, selected=A)
        try:
            entry.options["vehicle_profile_selected_at"] = (
                now + selected_at_offset if isinstance(selected_at_offset, int) else selected_at_offset
            )
            clock[0] = now - 87
            charging(coordinator)
            coordinator.async_update_listeners()
            # Enabling recognition later cannot retrospectively promote an
            # already captured manual claim to automatic cycle attribution.
            entry.options["vehicle_auto_selection"] = True
            m.confirm_automatic_vehicle(hass, entry, A)
            choose(hass, entry, clock, None)
            assert "vehicle_profile_id" not in m.loss_vehicle_options(hass, entry)
            record = complete(coordinator, now)
            assert m.attributed_session(hass, entry, A, record) is (selected_at_offset == -200)
        finally:
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


def test_same_runtime_handoff_ignores_empty_initial_poll_and_runtime_gap(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch)
        try:
            charging(coordinator)
            clock[0] = now - 87
            choose(hass, entry, clock, A)
            unsubscribe()
            runtime = entry.runtime_data
            entry.runtime_data = None
            assert m.loss_vehicle_options(hass, entry)["vehicle_profile_id"] == A
            entry.runtime_data = runtime
            replacement = Coordinator()
            replacement.data.values.clear()
            entry.runtime_data.coordinator = replacement
            unsubscribe = m.track_loss_attribution(hass, entry)
            replacement.data = coordinator.data
            replacement.async_update_listeners()
            record = complete(replacement, now)
            assert m.attributed_session(hass, entry, A, record) is True
        finally:
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("interruption", ["other_profile", "unknown", "disconnect", "identity", "source_edit"])
def test_conflicting_or_lost_cycle_cannot_be_reassigned(tmp_path, monkeypatch, interruption):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch)
        observer = None
        try:
            charging(coordinator)
            clock[0] = now - 87
            choose(hass, entry, clock, A)
            if interruption == "other_profile":
                clock[0] += 1
                choose(hass, entry, clock, None)
                choose(hass, entry, clock, B)
            elif interruption == "source_edit":
                entry.options["vehicle_profiles"][A]["options"]["vehicle_last_charge_energy_entity"] = "sensor.changed"
            elif interruption == "identity":
                entry.runtime_data.identity.serial_number = "different-wallbox"
            elif interruption == "disconnect":
                entry.runtime_data.client.connected = False
            else:
                coordinator.last_update_success = False
            coordinator.async_update_listeners()
            # Unknown/reconnect cannot launder a prior conflict into new proof.
            coordinator.last_update_success = False
            coordinator.async_update_listeners()
            coordinator.last_update_success = True
            entry.runtime_data.client.connected = True
            coordinator.async_update_listeners()
            m.confirm_automatic_vehicle(hass, entry, A)
            record = complete(coordinator, now)
            assert m.attributed_session(hass, entry, A, record) is not True
            assert m.attributed_session(hass, entry, B, record) is not True
            observer = ChargeLossObserver(hass, entry)
            await observer.async_start()
            assert not observer.can_assign
            with pytest.raises(ServiceValidationError):
                await observer.async_assign()
        finally:
            if observer is not None:
                await observer.async_stop()
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


def test_old_completed_session_keeps_manual_time_guard_and_new_auto_claim_cannot_take_it(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch, automatic=False, selected=A)
        # Restore an existing completed record before there is live cycle proof.
        unsubscribe()
        old = complete(coordinator, now - 300)
        m.forget_loss_attribution(hass, entry.entry_id)
        unsubscribe = m.track_loss_attribution(hass, entry)
        first = None
        try:
            assert m.attributed_session(hass, entry, A, old) is None
            entry.options["vehicle_profile_selected_at"] = now - 500
            first = ChargeLossObserver(hass, entry)
            await first.async_start()
            assert first.can_assign
            await first.async_stop()
            entry.options["vehicle_auto_selection"] = True
            clock[0] = now - 87
            charging(coordinator)
            choose(hass, entry, clock, B)
            assert m.attributed_session(hass, entry, B, old) is not True
            late = ChargeLossObserver(hass, entry)
            await late.async_start()
            assert not late.can_assign
            await late.async_stop()
        finally:
            if first is not None:
                await first.async_stop()
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


def test_missing_initial_absence_or_full_unload_never_establishes_automatic_ownership(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator, entry, clock, now, unsubscribe = setup(hass, monkeypatch)
        try:
            charging(coordinator)
            clock[0] = now - 87
            choose(hass, entry, clock, A)
            unsubscribe()
            m.forget_loss_attribution(hass, entry.entry_id)
            unsubscribe = m.track_loss_attribution(hass, entry)
            m.confirm_automatic_vehicle(hass, entry, A)
            record = complete(coordinator, now)
            assert m.attributed_session(hass, entry, A, record) is False
            # A genuinely observed new cycle can be attributed again.
            charging(coordinator)
            clock[0] = now + 5
            m.confirm_automatic_vehicle(hass, entry, A)
            next_record = complete(coordinator, now + 100, connected_offset=-100, start_offset=-90)
            assert m.attributed_session(hass, entry, A, next_record) is True
        finally:
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())
