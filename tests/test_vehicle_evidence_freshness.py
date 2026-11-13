"""Permission evidence retains measurement age instead of renewing cached data."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.vehicle_detection import VehicleEvidence
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.detection import (
    BavarianDetectionSource,
)
from tests.test_bavarian_detection import local_hass, plug, tracker
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A


def test_manual_evidence_keeps_report_time_and_updates_on_unchanged_reports(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        options = detection_catalog()
        callbacks = []
        evidence = VehicleEvidence(hass, SimpleNamespace(options=options))
        unsubscribe = evidence.subscribe(lambda *_: callbacks.append(evidence.snapshot(A)))
        try:
            assert evidence.snapshot("missing").connected_at is None
            for name, state in (("first", "on"), ("second", "off")):
                hass.states.async_set(f"binary_sensor.{name}_plug", state)
                hass.states.async_set(f"device_tracker.{name}", "home")
            await hass.async_block_till_done()
            old = evidence.snapshot(A).connected_at
            assert old == hass.states.get("binary_sensor.first_plug").last_reported
            assert evidence.selected() == A
            hass.states.async_set("binary_sensor.first_plug", "on")
            await hass.async_block_till_done()
            current = evidence.snapshot(A).connected_at
            assert current > old
            assert callbacks[-1].connected_at == current
            unsubscribe()
            count = len(callbacks)
            hass.states.async_set("binary_sensor.first_plug", "off")
            await hass.async_block_till_done()
            assert len(callbacks) == count
        finally:
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


def test_bavarian_measurement_timestamp_is_not_renewed_by_new_ha_reports(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        measured = dt_util.utcnow() - timedelta(minutes=10)
        plug(hass, timestamp=measured.isoformat())
        tracker(hass)
        source = BavarianDetectionSource(hass, "car")
        callbacks = []
        unsubscribe = source.subscribe(callbacks.append)
        try:
            assert source.snapshot.connected is True
            assert source.snapshot.connected_at == measured
            plug(hass, timestamp=measured.isoformat())
            await hass.async_block_till_done()
            assert callbacks[-1].connected_at == measured
            assert source.snapshot.connected_at < hass.states.get("binary_sensor.car_plug").last_reported
            fresh = dt_util.utcnow()
            plug(hass, timestamp=fresh.isoformat())
            await hass.async_block_till_done()
            assert source.snapshot.connected_at == fresh
        finally:
            unsubscribe()
            await hass.async_stop()
    asyncio.run(run())


def test_bavarian_without_measurement_timestamp_is_no_cable_evidence(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass, timestamp=None)
            tracker(hass)
            evidence = BavarianDetectionSource(hass, "car").snapshot
            assert evidence.connected is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("timestamp", ["invalid", None, "2026-01-01T00:00:00", "2999-01-01T00:00:00+00:00"])
def test_invalid_supplied_bmw_timestamp_never_becomes_fresh_permission(tmp_path, timestamp):
    async def run():
        hass = local_hass(tmp_path)
        try:
            plug(hass, timestamp=timestamp)
            tracker(hass)
            evidence = BavarianDetectionSource(hass, "car").snapshot
            assert evidence.connected is not True or evidence.connected_at is None
        finally:
            await hass.async_stop()
    asyncio.run(run())
