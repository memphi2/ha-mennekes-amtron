"""A car plugged in long after parking is recognized by its plug report's timing."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu import vehicle_binding as binding_module
from custom_components.mennekes_amtron_rtu.vehicle_detection import (
    VehicleDetectionObserver,
    VehicleEvidence,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources import (
    PLUG_WINDOW,
    VehicleDetection,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.detection import (
    BavarianDetectionSource,
)
from tests.test_bavarian_detection import local_hass, plug, tracker
from tests.test_plug_charge_observer import observer_harness, report
from tests.test_vehicle_binding import binding_entry, settle
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A, B

# Parked at 17:48, plugged in at 18:54.
PARKED = 66 * 60


def parked(hass, entity_id, value="home", seconds=PARKED):
    hass.states.async_set(entity_id, value, timestamp=time.time() - seconds)


NOW = dt_util.utcnow()


@pytest.mark.parametrize(("evidence", "plugged_at", "expected"), [
    (VehicleDetection(True, True, NOW), None, True),
    (VehicleDetection(False, None, NOW, parked_home=True), NOW, False),
    (VehicleDetection(True, False, NOW, parked_home=True), NOW, False),
    (VehicleDetection(None, True, NOW), NOW, None),
    (VehicleDetection(True, None, NOW - timedelta(seconds=90), parked_home=True), NOW, True),
    (VehicleDetection(True, None, NOW + timedelta(seconds=90), parked_home=True), NOW, True),
    (VehicleDetection(True, None, NOW - PLUG_WINDOW - timedelta(seconds=1), parked_home=True), NOW, None),
    (VehicleDetection(True, None, NOW, parked_home=True), None, None),
    (VehicleDetection(True, None, None, parked_home=True), NOW, None),
    (VehicleDetection(True, None, NOW, parked_home=False), NOW, None),
    (VehicleDetection(True, None, NOW, parked_home=None), NOW, None),
])
def test_a_stale_home_counts_only_for_a_plug_report_at_the_plug_in(evidence, plugged_at, expected):
    assert evidence.at_home(plugged_at) is expected


def test_bavarian_keeps_the_last_location_of_a_parked_car(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass)
            entity_id = tracker(hass)
            state = hass.states.get(entity_id)
            hass.states.async_set(entity_id, "home", state.attributes, timestamp=time.time() - PARKED)
            snapshot = BavarianDetectionSource(hass, "car").snapshot
            assert (snapshot.connected, snapshot.home, snapshot.parked_home) == (True, None, True)
            # Parked elsewhere, or a restored position, never counts as home.
            hass.states.async_set(entity_id, "not_home", {**state.attributes, "latitude": 51.0}, timestamp=time.time() - PARKED)
            assert BavarianDetectionSource(hass, "car").snapshot.parked_home is False
            hass.states.async_set(entity_id, "home", {**state.attributes, "restored": True})
            assert BavarianDetectionSource(hass, "car").snapshot.parked_home is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_selection_accepts_a_parked_car_only_for_this_plug_in(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        evidence = VehicleEvidence(hass, SimpleNamespace(options=detection_catalog()))
        parked(hass, "device_tracker.first")
        hass.states.async_set("binary_sensor.first_plug", "on")
        hass.states.async_set("device_tracker.second", "home")
        hass.states.async_set("binary_sensor.second_plug", "off")
        now = dt_util.utcnow()
        assert evidence.selected() is None
        assert evidence.selected(now) == A
        assert evidence.selected(now + PLUG_WINDOW + timedelta(seconds=5)) is None
        # The other parked car reporting the same plug-in makes it ambiguous.
        parked(hass, "device_tracker.second")
        hass.states.async_set("binary_sensor.second_plug", "on")
        assert evidence.selected(now) is None
        assert evidence.effective(B, plugged_at=now) is None
        await hass.async_stop()
    asyncio.run(run())


def test_plug_and_charge_grants_a_car_plugged_in_long_after_parking(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            parked(h.hass, "device_tracker.first")
            await report(h)
            assert h.observer.status == "waiting_release"
            assert len(h.timers) == 1
            # A plug report from well before the wallbox saw the car is old.
            h.control.plug_charge["since"] = (dt_util.utcnow() + PLUG_WINDOW + timedelta(minutes=1)).isoformat()
            h.coordinator.async_update_listeners()
            assert h.observer.status == "waiting_detection"
    asyncio.run(run())


def test_the_binding_confirms_a_parked_car_and_tells_when_it_arrived(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry()
        parked(hass, "device_tracker.first")
        hass.states.async_set("binary_sensor.first_plug", "off")
        parked(hass, "device_tracker.second")
        hass.states.async_set("binary_sensor.second_plug", "off")
        binding = binding_module.VehicleBinding(hass, entry)
        await binding.async_start()
        coordinator.plug(False)
        await settle(hass)
        assert binding.plugged_at is None
        coordinator.plug(True)
        await settle(hass)
        assert binding.resting
        arrived = binding.plugged_at
        assert arrived is not None
        hass.states.async_set("binary_sensor.first_plug", "on")
        await settle(hass)
        assert not binding.resting
        assert binding.plugged_at == arrived
        binding.stop()
        await hass.async_stop()
    asyncio.run(run())


def test_automatic_selection_takes_the_plug_in_time_from_the_binding(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        options = detection_catalog()
        now = dt_util.utcnow()
        entry = SimpleNamespace(options=options, runtime_data=SimpleNamespace(
            vehicle_binding=SimpleNamespace(plugged_at=now),
        ))
        assert VehicleDetectionObserver(hass, entry)._plugged_at() == now
        entry.runtime_data.vehicle_binding = None
        assert VehicleDetectionObserver(hass, entry)._plugged_at() is None
        await hass.async_stop()
    asyncio.run(run())
