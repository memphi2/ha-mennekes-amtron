"""Local cable/GPS hints remain bounded, optional and tied to one vehicle."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.vehicle_sources import (
    DETECTION_MAX_AGE_SECONDS,
    VehicleDetection,
    profile_capabilities,
    provider_definitions,
    provider_identity,
    resolve_detection_source,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.detection import (
    HOME_ENTITY,
    PLUG_DESCRIPTOR,
    BavarianDetectionSource,
    detection_capabilities,
)
from tests.test_bavarian_history import VIN, vehicle_registry


def local_hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.data[dr.DATA_REGISTRY] = vehicle_registry()
    entities = {}
    hass.data[er.DATA_REGISTRY] = SimpleNamespace(
        entities=entities,
        async_get_entity_id=lambda domain, platform, unique: next((
            entry.entity_id for entry in entities.values()
            if entry.entity_id.startswith(domain + ".") and platform == "bavariandata" and entry.unique_id == unique
        ), None),
        async_get=entities.get,
    )
    hass.states.async_set(HOME_ENTITY, "0", {"latitude": 50.0, "longitude": 8.0, "radius": 100.0})
    return hass


def plug(hass, value="on", *, entity_id="binary_sensor.car_plug", **attrs):
    hass.data[er.DATA_REGISTRY].entities[entity_id] = SimpleNamespace(
        unique_id=f"{VIN}_{PLUG_DESCRIPTOR}", entity_id=entity_id, device_id="car", disabled_by=None,
    )
    hass.states.async_set(entity_id, value, {"descriptor": PLUG_DESCRIPTOR, **attrs})
    return entity_id


def tracker(hass, value="home", *, entity_id="device_tracker.car", **attrs):
    hass.data[er.DATA_REGISTRY].entities[entity_id] = SimpleNamespace(
        unique_id=f"{VIN}_tracker", entity_id=entity_id, device_id="car", disabled_by=None,
    )
    hass.states.async_set(entity_id, value, {
        "latitude": 50.0, "longitude": 8.0, "source_type": "gps", "gps_accuracy": 0, **attrs,
    })
    return entity_id


def test_registered_cable_and_tracker_enable_detection_without_guessing_other_fields(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            assert detection_capabilities(hass, "car") == frozenset()
            assert [provider.id for provider in provider_definitions("detection")] == ["bavariandata"]
            plug(hass, "unavailable")
            assert "detection" not in profile_capabilities(hass, "bavariandata", "car")
            tracker(hass, "unknown")
            assert "detection" in profile_capabilities(hass, "bavariandata", "car")
            source = resolve_detection_source(hass, {"vehicle_provider": "bavariandata", "vehicle_device": "car", "vehicle_source_identity": ""})
            assert source.available
            assert source.snapshot == VehicleDetection()
            assert VIN not in source.source_key
            assert source.source_key == provider_identity(hass, "bavariandata", "car")
            for provider, device in (("none", "car"), ("bavariandata", ""), ("another", "car")):
                assert resolve_detection_source(hass, {"vehicle_provider": provider, "vehicle_device": device}) is None
            assert resolve_detection_source(hass, {}) is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_snapshot_uses_explicit_plug_and_coordinates_not_charging_or_tracker_label(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass)
            tracker(hass, "not_home")  # A stale or translated label is irrelevant.
            source = BavarianDetectionSource(hass, "car")
            assert source.snapshot == VehicleDetection(True, True)
            plug(hass, "off")
            tracker(hass, "home", latitude=51.0)
            assert source.snapshot == VehicleDetection(False, False)
            plug(hass, "charging")
            assert source.snapshot.connected is None
            hass.states.async_remove("binary_sensor.car_plug")
            assert source.snapshot.connected is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("corruption", ["wrong_device", "disabled", "wrong_vin", "missing_entry", "missing_device"])
def test_detection_never_claims_a_different_or_disabled_vehicle(tmp_path, corruption):
    async def run():
        hass = local_hass(tmp_path)
        try:
            entity_id = plug(hass)
            tracker(hass)
            entries = hass.data[er.DATA_REGISTRY].entities
            if corruption == "wrong_device":
                entries[entity_id].device_id = "other"
            elif corruption == "disabled":
                entries[entity_id].disabled_by = "user"
            elif corruption == "wrong_vin":
                entries[entity_id].unique_id = "ANOTHERVIN_" + PLUG_DESCRIPTOR
            elif corruption == "missing_entry":
                hass.data[er.DATA_REGISTRY].async_get = lambda _: None
            else:
                hass.data[dr.DATA_REGISTRY].async_get.return_value = None
            assert "detection" not in detection_capabilities(hass, "car")
            assert BavarianDetectionSource(hass, "car").snapshot.connected is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("attrs", [
    {"restored": True}, {"descriptor": "other"}, {"timestamp": "invalid"},
    {"timestamp": 1700000000}, {"timestamp": "2020-01-01T00:00:00Z"},
    {"timestamp": "2030-01-01T00:00:00Z"}, {"timestamp": "2026-11-07T12:00:00"},
])
def test_untrustworthy_cable_evidence_is_unknown(tmp_path, attrs):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass, **attrs)
            assert BavarianDetectionSource(hass, "car").snapshot.connected is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_cable_source_timestamp_cannot_be_refreshed_by_unrelated_ha_reports(tmp_path, monkeypatch):
    async def run():
        hass = local_hass(tmp_path)
        try:
            now = dt_util.utcnow()
            plug(hass, timestamp=(now - timedelta(minutes=29)).isoformat())
            assert BavarianDetectionSource(hass, "car").snapshot.connected is True
            with monkeypatch.context() as patch:
                patch.setattr(dt_util, "utcnow", lambda: now + timedelta(minutes=2))
                assert BavarianDetectionSource(hass, "car").snapshot.connected is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("attrs", [
    {"restored": True}, {"source_type": "router"}, {"latitude": None},
    {"latitude": "50.0"}, {"latitude": 91}, {"longitude": -181},
    {"latitude": float("nan")}, {"longitude": float("inf")},
    {"gps_accuracy": -1}, {"gps_accuracy": float("nan")}, {"latitude": True},
    {"gps_accuracy": 10 ** 500},
])
def test_invalid_tracker_data_never_asserts_home_or_away(tmp_path, attrs):
    async def run():
        hass = local_hass(tmp_path)
        try:
            tracker(hass, **attrs)
            assert BavarianDetectionSource(hass, "car").snapshot.home is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize(("state", "attrs"), [
    (None, {}), ("unavailable", {}), ("unknown", {}),
    ("0", {"latitude": 91, "longitude": 8.0, "radius": 100}),
    ("0", {"latitude": 50.0, "longitude": 8.0}),
    ("0", {"latitude": 50.0, "longitude": 8.0, "radius": -1}),
    ("0", {"latitude": 50.0, "longitude": 8.0, "radius": 100, "passive": True}),
    ("0", {"latitude": 50.0, "longitude": 8.0, "radius": 100, "restored": True}),
])
def test_missing_or_invalid_home_zone_is_unknown(tmp_path, state, attrs):
    async def run():
        hass = local_hass(tmp_path)
        try:
            tracker(hass)
            if state is None:
                hass.states.async_remove(HOME_ENTITY)
            else:
                hass.states.async_set(HOME_ENTITY, state, attrs)
            assert BavarianDetectionSource(hass, "car").snapshot.home is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_gps_uncertainty_crossing_home_boundary_is_unknown(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            tracker(hass, gps_accuracy=101)
            assert BavarianDetectionSource(hass, "car").snapshot.home is None
            tracker(hass, latitude=51.0, gps_accuracy=101)
            assert BavarianDetectionSource(hass, "car").snapshot.home is False
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_snapshot_expires_without_events_and_rejects_future_reports(tmp_path, monkeypatch):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass)
            tracker(hass)
            source = BavarianDetectionSource(hass, "car")
            now = dt_util.utcnow()
            assert source.snapshot == VehicleDetection(True, True)
            with monkeypatch.context() as patch:
                patch.setattr(dt_util, "utcnow", lambda: now + timedelta(seconds=DETECTION_MAX_AGE_SECONDS + 1))
                assert source.snapshot == VehicleDetection()
            with monkeypatch.context() as patch:
                patch.setattr(dt_util, "utcnow", lambda: now - timedelta(seconds=1))
                assert source.snapshot == VehicleDetection()
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_event_snapshots_are_chronological_and_each_subscription_cleans_up(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass, "off")
            tracker(hass)
            source = BavarianDetectionSource(hass, "car")
            first, second = [], []
            unsub = source.subscribe(first.append)
            unsub2 = source.subscribe(second.append)
            plug(hass, "on")
            tracker(hass, latitude=51.0)
            plug(hass, "off")
            await hass.async_block_till_done()
            assert first == second == [VehicleDetection(True, True), VehicleDetection(True, False), VehicleDetection(False, False)]
            unsub()
            plug(hass, "off")  # state_reported also renews observations.
            await hass.async_block_till_done()
            assert len(first) == 3
            assert len(second) == 4
            hass.states.async_set(HOME_ENTITY, "0", {"latitude": 51.0, "longitude": 8.0, "radius": 100})
            await hass.async_block_till_done()
            assert second[-1] == VehicleDetection(False, True)
            unsub2()
            plug(hass, "on")
            hass.states.async_remove(HOME_ENTITY)
            await hass.async_block_till_done()
            assert len(second) == 5
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_entity_rename_move_and_removal_rebind_without_cross_vehicle_data(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            old = plug(hass)
            tracker(hass)
            registry = hass.data[er.DATA_REGISTRY]
            source = BavarianDetectionSource(hass, "car")
            values = []
            unsub = source.subscribe(values.append)
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": "sensor.unrelated"})
            await hass.async_block_till_done()
            assert values == []
            del registry.entities[old]
            new = plug(hass, entity_id="binary_sensor.renamed")
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": new, "old_entity_id": old})
            await hass.async_block_till_done()
            assert values[-1] == VehicleDetection(True, True)
            registry.entities[new].device_id = "other"
            hass.states.async_set(new, "off", {"descriptor": PLUG_DESCRIPTOR})
            await hass.async_block_till_done()
            assert values[-1] == VehicleDetection(None, True)
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": new})
            await hass.async_block_till_done()
            hass.states.async_remove("device_tracker.car")
            await hass.async_block_till_done()
            assert values[-1] == VehicleDetection()
            unsub()
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_pinned_vehicle_can_appear_late_but_cannot_follow_another_vin(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass)
            tracker(hass)
            key = provider_identity(hass, "bavariandata", "car")
            device = hass.data[dr.DATA_REGISTRY].async_get.return_value
            hass.data[dr.DATA_REGISTRY].async_get.return_value = None
            source = BavarianDetectionSource(hass, "car", expected_source_key=key)
            assert not source.available
            assert source.source_key == key
            values = []
            unsub = source.subscribe(values.append)
            hass.data[dr.DATA_REGISTRY].async_get.return_value = device
            hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"action": "update", "device_id": "car"})
            await hass.async_block_till_done()
            assert source.available
            assert values[-1] == VehicleDetection(True, True)
            device.identifiers = {("bavariandata", "WBA00000000000001")}
            hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"action": "update", "device_id": "car"})
            await hass.async_block_till_done()
            assert not source.available
            assert source.source_key == key
            assert source.snapshot == VehicleDetection()
            assert values[-1] == VehicleDetection()
            unsub()
        finally:
            await hass.async_stop()
    asyncio.run(run())
