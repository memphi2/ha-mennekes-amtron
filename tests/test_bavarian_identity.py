"""All vehicle sources retain the same pinned identity across registry changes."""

from functools import partial
from types import SimpleNamespace

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata import (
    BavarianChargeHistorySource,
    BavarianDepartureSource,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.detection import (
    BavarianDetectionSource,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.telemetry import (
    BavarianTelemetrySource,
)
from tests.test_bavarian_history import VIN, vehicle_registry

SOURCE_KEY = "bavariandata:car:d01b0122196e9432bfe54f997e427a4681ac0ea8719a65e6f665f06e4f5d98b1"
OTHER_VIN = "WBA98765432109876"


@pytest.fixture(params=[
    BavarianTelemetrySource,
    BavarianDetectionSource,
    BavarianChargeHistorySource,
    partial(BavarianDepartureSource, time_zone="Europe/Berlin", timer1_days=(), timer2_days=()),
], ids=["telemetry", "detection", "history", "departure"])
def source_factory(request):
    return request.param


@pytest.mark.parametrize("pinned", [False, True])
def test_source_keeps_original_identity_when_registry_device_is_removed_or_rebound(source_factory, pinned):
    registry = vehicle_registry()
    hass = SimpleNamespace(data={dr.DATA_REGISTRY: registry})
    device = registry.async_get.return_value
    source = source_factory(hass, "car", expected_source_key=SOURCE_KEY if pinned else None)
    assert source.available
    assert source.source_key == SOURCE_KEY

    registry.async_get.return_value = None
    assert not source.available
    assert source.source_key == SOURCE_KEY
    registry.async_get.return_value = device
    device.identifiers = {("bavariandata", OTHER_VIN)}
    assert not source.available
    assert source.source_key == SOURCE_KEY
    device.identifiers = {("bavariandata", VIN)}
    assert source.available
    assert source.source_key == SOURCE_KEY


def test_persisted_pin_waits_for_its_vehicle_without_binding_a_replacement(source_factory):
    hass = SimpleNamespace(data={})
    source = source_factory(hass, "car", expected_source_key=SOURCE_KEY)
    assert not source.available
    assert source.source_key == SOURCE_KEY
    registry = vehicle_registry(identifiers={("bavariandata", OTHER_VIN)})
    hass.data[dr.DATA_REGISTRY] = registry
    assert not source.available
    assert source.source_key == SOURCE_KEY
    registry.async_get.return_value.identifiers = {("bavariandata", VIN)}
    assert source.available
    assert source.source_key == SOURCE_KEY


def test_unpinned_source_binds_once_when_the_registry_appears(source_factory):
    hass = SimpleNamespace(data={})
    source = source_factory(hass, "car")
    assert not source.available
    assert source.source_key == "bavariandata:car"
    registry = vehicle_registry()
    hass.data[dr.DATA_REGISTRY] = registry
    assert source.available
    assert source.source_key == SOURCE_KEY
    registry.async_get.return_value.identifiers = {("bavariandata", OTHER_VIN)}
    assert not source.available
    assert source.source_key == SOURCE_KEY
