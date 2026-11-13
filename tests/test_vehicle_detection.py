"""Automatic association is opt-in, unique, fresh and subordinate to manual choice."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu import vehicle_detection as m
from custom_components.mennekes_amtron_rtu.vehicle_profiles import activate_profile
from custom_components.mennekes_amtron_rtu.vehicle_sources import VehicleDetection
from tests.test_vehicle_profile_runtime import A, B, catalog


def detection_catalog():
    raw = catalog()
    raw["vehicle_auto_selection"] = True
    for key, name in ((A, "first"), (B, "second")):
        raw["vehicle_profiles"][key]["detection"] = {
            "source": "entities", "connected_entity": f"binary_sensor.{name}_plug",
            "home_entity": f"device_tracker.{name}",
        }
    return raw


@pytest.mark.parametrize(("entity", "value", "home", "expected"), [
    ("binary_sensor.plug", "on", False, True),
    ("binary_sensor.plug", "off", False, False),
    ("binary_sensor.home", "on", True, True),
    ("binary_sensor.plug", "unknown", False, None),
    ("device_tracker.car", "home", True, True),
    ("person.driver", "not_home", True, False),
    ("person.driver", "Work", True, False),
    ("device_tracker.car", "unavailable", True, None),
    ("device_tracker.car", "home", False, None),
    ("sensor.plug", "on", False, None),
])
def test_only_explicit_connection_and_presence_states_qualify(entity, value, home, expected):
    assert m.entity_signal(State(entity, value), home=home) is expected


@pytest.mark.parametrize(("value", "expected"), [
    ("CONNECTED", True), ("LOCKED", True), ("DISCONNECTED", False),
    ("unknown", None), ("unavailable", None), ("", None),
])
def test_a_text_sensor_is_connected_only_in_its_chosen_states(value, expected):
    state = State("sensor.port", value)
    assert m.entity_signal(state, home=False, connected_states=("CONNECTED", "LOCKED")) is expected
    assert m.entity_signal(state, home=True, connected_states=("CONNECTED",)) is None


def test_restored_stale_and_future_signals_are_unknown():
    assert m.entity_signal(None, home=True) is None
    assert m.entity_signal(State("binary_sensor.plug", "on", {"restored": True}), home=False) is None
    for offset in (-1801, 10):
        state = State("binary_sensor.plug", "on", last_reported=dt_util.utcnow() + timedelta(seconds=offset))
        assert m.entity_signal(state, home=False) is None


def test_unique_candidates_settle_and_manual_override_cancels_pending_work(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        entry = SimpleNamespace(options=detection_catalog())
        updates, timers = [], []

        def update(entry, *, options):
            entry.options = options
            updates.append(options)

        def schedule(_hass, delay, callback):
            assert delay == 3
            item = [callback, False]
            timers.append(item)
            return lambda: item.__setitem__(1, True)

        monkeypatch.setattr(hass.config_entries, "async_update_entry", update)
        monkeypatch.setattr(m, "async_call_later", schedule)
        for entity, value in (("binary_sensor.first_plug", "on"), ("device_tracker.first", "home"),
                              ("binary_sensor.second_plug", "off"), ("device_tracker.second", "home")):
            hass.states.async_set(entity, value)
        observer = m.VehicleDetectionObserver(hass, entry)
        try:
            assert observer.selected() == A
            m.prepare_vehicle_selection(hass, entry)
            assert updates == []
            observer.start()
            hass.states.async_set("binary_sensor.first_plug", "on")
            await hass.async_block_till_done()
            assert updates == []
            hass.states.async_set("binary_sensor.second_plug", "on")
            await hass.async_block_till_done()
            assert entry.options["active_vehicle_profile"] is None
            assert entry.options["timed_goal_reset_pending"] is True
            assert observer.selected() is None
            hass.states.async_set("binary_sensor.first_plug", "off")
            await hass.async_block_till_done()
            assert entry.options["active_vehicle_profile"] is None
            assert len(timers) == 1
            first_timer = timers[-1][0]
            hass.states.async_set("binary_sensor.first_plug", "on")
            await hass.async_block_till_done()
            assert timers[-1][1]
            hass.states.async_set("binary_sensor.first_plug", "off")
            await hass.async_block_till_done()
            first_timer(None)
            assert entry.options["active_vehicle_profile"] is None
            timers[-1][0](None)
            assert entry.options["active_vehicle_profile"] == B
            # A merely unknown car does not end the selection: that reloads.
            hass.states.async_set("binary_sensor.first_plug", "unavailable")
            await hass.async_block_till_done()
            assert entry.options["active_vehicle_profile"] == B
            # Another car reporting itself plugged in does.
            hass.states.async_set("binary_sensor.first_plug", "on")
            await hass.async_block_till_done()
            assert entry.options["active_vehicle_profile"] is None
            hass.states.async_set("binary_sensor.second_plug", "off")
            await hass.async_block_till_done()
            assert len(timers) == 3
            entry.options = activate_profile(entry.options, A)
            entry.options["vehicle_auto_selection"] = False
            timers[-1][0](None)
            assert entry.options["active_vehicle_profile"] == A
            observer._changed()
            assert entry.options["active_vehicle_profile"] == A
        finally:
            observer.stop()
            await hass.async_stop()

    asyncio.run(run())


def test_startup_invalidates_wrong_car_before_goals_restore_but_does_not_skip_settling(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        entry = SimpleNamespace(options=detection_catalog())
        monkeypatch.setattr(hass.config_entries, "async_update_entry", lambda entry, *, options: setattr(entry, "options", options))
        try:
            for name, plugged in (("first", "off"), ("second", "on")):
                hass.states.async_set(f"binary_sensor.{name}_plug", plugged)
                hass.states.async_set(f"device_tracker.{name}", "home")
            m.prepare_vehicle_selection(hass, entry)
            assert entry.options["active_vehicle_profile"] is None
            assert entry.options["timed_goal_reset_pending"] is True
            m.prepare_vehicle_selection(hass, entry)
            assert entry.options["active_vehicle_profile"] is None
            entry.options = activate_profile(entry.options, A)
            entry.options["vehicle_auto_selection"] = False
            m.prepare_vehicle_selection(hass, entry)
            assert entry.options["active_vehicle_profile"] == A
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_profile_source_subscription_staleness_and_config_changes_are_isolated(tmp_path, monkeypatch):
    class Source:
        snapshot = VehicleDetection(True, True)

        def subscribe(self, callback):
            self.callback = callback
            return lambda: setattr(self, "callback", None)

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        entry = SimpleNamespace(options=detection_catalog())
        entry.options["vehicle_profiles"][A]["detection"] = {"source": "profile"}
        entry.options["vehicle_profiles"][A]["provider"] = "bavariandata"
        entry.options["vehicle_profiles"][A]["device_id"] = "vehicle"
        entry.options["vehicle_profiles"][B].pop("detection")
        source = Source()
        monkeypatch.setattr(m, "resolve_detection_source", lambda *_: source)
        monkeypatch.setattr(hass.config_entries, "async_update_entry", lambda entry, *, options: setattr(entry, "options", options))
        observer = m.VehicleDetectionObserver(hass, entry)
        try:
            assert m.automatic_selection_available(entry.options)
            observer.start()
            assert observer.selected() == A
            source.snapshot = VehicleDetection()
            source.callback(source.snapshot)
            # Stale evidence keeps the selected car instead of reloading.
            assert entry.options["active_vehicle_profile"] == A
            entry.options = activate_profile(entry.options, None)
            # A queued old callback cannot select from a modified catalog.
            entry.options["vehicle_profiles"][A]["name"] = "Renamed"
            source.snapshot = VehicleDetection(True, True)
            source.callback(source.snapshot)
            assert entry.options["active_vehicle_profile"] is None
            observer.stop()
            assert source.callback is None
            monkeypatch.setattr(m, "resolve_detection_source", lambda *_: None)
            missing = m.VehicleDetectionObserver(hass, entry)
            assert missing.selected() is None
            missing.start()
            missing.stop()
            entry.options = catalog()
            assert not m.automatic_selection_available(entry.options)
            assert m.VehicleDetectionObserver(hass, entry).selected() is None
        finally:
            observer.stop()
            await hass.async_stop()

    asyncio.run(run())


def test_the_active_car_stays_until_an_unknown_car_is_at_the_wallbox(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry = SimpleNamespace(options=detection_catalog())
        for entity, value in (("binary_sensor.first_plug", "off"), ("device_tracker.first", "home"),
                              ("binary_sensor.second_plug", "off"), ("device_tracker.second", "home")):
            hass.states.async_set(entity, value)
        evidence = m.VehicleEvidence(hass, entry)
        try:
            assert evidence.selected() is None
            # Unplugged or unknown wallbox state: keep the car and its goals.
            assert evidence.effective(A) == A
            assert evidence.effective(A, wallbox_connected=False) == A
            # Something is plugged in, but the active car says it is not.
            assert evidence.effective(A, wallbox_connected=True) is None
            hass.states.async_set("binary_sensor.first_plug", "unavailable")
            assert evidence.effective(A, wallbox_connected=True) == A
            assert evidence.effective(None, wallbox_connected=True) is None
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_one_failing_evidence_listener_does_not_hide_the_event(tmp_path, caplog):
    from custom_components.mennekes_amtron_rtu.vehicle_detection import VehicleEvidence

    evidence = VehicleEvidence.__new__(VehicleEvidence)
    seen = []

    def broken(_event):
        raise RuntimeError("boom")

    evidence._listeners = [broken, seen.append]
    evidence._notify("event")
    assert seen == ["event"]
    assert "Vehicle evidence listener failed" in caplog.text
