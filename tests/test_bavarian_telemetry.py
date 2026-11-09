"""BavarianData profile values stay local, vehicle-bound and chronological."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.vehicle_sources import (
    ProviderSelection,
    VehicleTelemetry,
    profile_capabilities,
    profile_entities,
    provider_identity,
    provider_selection,
    resolve_departure_source,
    resolve_history_source,
    resolve_telemetry_source,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.telemetry import (
    CAPACITY_DESCRIPTOR,
    ESTIMATE_DESCRIPTOR,
    LIMIT_DESCRIPTOR,
    SOC_DESCRIPTORS,
    BavarianTelemetrySource,
    telemetry_capabilities,
    telemetry_entities,
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
            if domain == "sensor" and platform == "bavariandata" and entry.unique_id == unique
        ), None),
        async_get=entities.get,
    )
    return hass


def publish(hass, descriptor, value, *, entity_id=None, device="car", vin=VIN, unit=None, **attributes):
    entity_id = entity_id or "sensor." + descriptor.rsplit(".", 1)[-1].lower()
    hass.data[er.DATA_REGISTRY].entities[entity_id] = SimpleNamespace(
        unique_id=f"{vin}_{descriptor}", entity_id=entity_id, device_id=device, disabled_by=None,
    )
    hass.states.async_set(entity_id, value, {
        "descriptor": descriptor,
        "unit_of_measurement": unit or ("kWh" if descriptor == CAPACITY_DESCRIPTOR else "%"),
        **attributes,
    })
    return entity_id


def test_profile_selection_is_shared_only_when_explicit(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            selected = {"vehicle_provider": "bavariandata", "vehicle_device": "car"}
            for capability in ("soc", "charge_limit", "capacity"):
                assert provider_selection(selected, capability) == ProviderSelection("bavariandata", "car")
            for feature, resolver in (("timed", resolve_departure_source), ("charge_loss", resolve_history_source)):
                assert resolver(hass, selected) is None
                assert resolver(hass, {**selected, f"{feature}_vehicle_provider": "profile"}) is not None
            source = resolve_telemetry_source(hass, selected)
            assert source is not None
            assert source.available
            assert VIN not in source.source_key
            assert source.source_key.startswith("bavariandata:car:")
            assert source.snapshot == VehicleTelemetry()
            # Optional identity was absent from the first profile schema. Its
            # projection uses an empty string, which must retain legacy binding.
            unpinned = {**selected, "vehicle_source_identity": ""}
            assert resolve_telemetry_source(hass, unpinned).available
            assert resolve_departure_source(hass, {**unpinned, "timed_vehicle_provider": "profile"}).available
            assert resolve_history_source(hass, {**unpinned, "charge_loss_vehicle_provider": "profile"}).available
            for provider, device in (("unsupported", "car"), ("none", "car"), ("bavariandata", "")):
                assert resolve_telemetry_source(hass, {"vehicle_provider": provider, "vehicle_device": device}) is None
            assert provider_selection({"vehicle_provider": 123, "vehicle_device": None}, "soc") == ProviderSelection("none", "")
            assert profile_capabilities(hass, "unsupported", "car") == frozenset()
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_detects_registered_fields_without_values_and_never_guesses_nominal_capacity(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            publish(hass, SOC_DESCRIPTORS[0], "unavailable")
            publish(hass, LIMIT_DESCRIPTOR, "unknown")
            publish(hass, "vehicle.drivetrain.batteryManagement.batterySizeMax", 90)
            source = BavarianTelemetrySource(hass, "car")
            assert source.snapshot == VehicleTelemetry()
            # Without climate timer entities the car offers no departures.
            assert profile_capabilities(hass, "bavariandata", "car") == frozenset({"soc", "charge_limit", "charge_history"})
            publish(hass, CAPACITY_DESCRIPTOR, 82000, unit="Wh")
            assert source.snapshot.capacity_kwh == 82
            assert "capacity" in profile_capabilities(hass, "bavariandata", "car")
            # The options show which entity each value comes from.
            assert profile_entities(hass, "bavariandata", "car") == {
                "soc": "sensor.header", "charge_limit": "sensor.target", "capacity": "sensor.maxenergy",
            }
            # BavarianData's prediction comes first.
            publish(hass, ESTIMATE_DESCRIPTOR, 63.5, entity_id="sensor.i4_predicted")
            assert source.snapshot.soc_percent == 63.5
            assert profile_entities(hass, "bavariandata", "car")["soc"] == "sensor.i4_predicted"
            assert profile_entities(hass, "none", "car") == {}
            hass.data[dr.DATA_REGISTRY].async_get.return_value = None
            assert telemetry_entities(hass, "car") == {}
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("corruption", ["wrong_device", "disabled", "wrong_descriptor", "restored", "wrong_unit", "unknown", "unavailable", "nan", "infinity", "-1", "101", "missing_state", "missing_entry"])
def test_invalid_soc_has_no_authority(tmp_path, corruption):
    async def run():
        hass = local_hass(tmp_path)
        try:
            entity = publish(hass, SOC_DESCRIPTORS[0], 55)
            entry = hass.data[er.DATA_REGISTRY].entities[entity]
            if corruption == "wrong_device":
                entry.device_id = "another-car"
            elif corruption == "disabled":
                entry.disabled_by = "user"
            elif corruption in {"wrong_descriptor", "restored", "wrong_unit"}:
                attr = {"descriptor": SOC_DESCRIPTORS[0], "unit_of_measurement": "%"}
                attr.update({"wrong_descriptor": {"descriptor": "another"}, "restored": {"restored": True}, "wrong_unit": {"unit_of_measurement": "kWh"}}[corruption])
                hass.states.async_set(entity, 55, attr)
            elif corruption == "missing_state":
                hass.states.async_remove(entity)
            elif corruption == "missing_entry":
                hass.data[er.DATA_REGISTRY].async_get = lambda _entity: None
            else:
                publish(hass, SOC_DESCRIPTORS[0], corruption)
            assert BavarianTelemetrySource(hass, "car").snapshot.soc_percent is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize(("descriptor", "value", "expected"), [
    (CAPACITY_DESCRIPTOR, 0, None), (CAPACITY_DESCRIPTOR, 251, None),
    (CAPACITY_DESCRIPTOR, 1, 1), (CAPACITY_DESCRIPTOR, 250, 250),
    (LIMIT_DESCRIPTOR, 0, None), (LIMIT_DESCRIPTOR, 80, 80),
])
def test_optional_values_obey_usable_ranges(tmp_path, descriptor, value, expected):
    async def run():
        hass = local_hass(tmp_path)
        try:
            publish(hass, descriptor, value)
            state = BavarianTelemetrySource(hass, "car").snapshot
            assert (state.capacity_kwh if descriptor == CAPACITY_DESCRIPTOR else state.maximum_percent) == expected
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_soc_alternative_only_used_when_primary_entity_absent(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            publish(hass, SOC_DESCRIPTORS[1], 60)
            source = BavarianTelemetrySource(hass, "car")
            assert source.snapshot.soc_percent == 60
            publish(hass, SOC_DESCRIPTORS[0], "unavailable")
            assert source.snapshot.soc_percent is None
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_event_snapshots_keep_brief_target_crossings_and_independent_subscribers(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            soc = publish(hass, SOC_DESCRIPTORS[0], 50)
            publish(hass, LIMIT_DESCRIPTOR, 80)
            publish(hass, CAPACITY_DESCRIPTOR, 82)
            source = BavarianTelemetrySource(hass, "car")
            first, second = [], []
            unsub = source.subscribe(first.append)
            unsub2 = source.subscribe(second.append)
            publish(hass, SOC_DESCRIPTORS[0], 85)
            publish(hass, SOC_DESCRIPTORS[0], 50)
            await hass.async_block_till_done()
            assert first == second == [VehicleTelemetry(85, 80, 82), VehicleTelemetry(50, 80, 82)]
            unsub()
            hass.states.async_set(soc, 50, dict(hass.states.get(soc).attributes))
            await hass.async_block_till_done()
            assert len(first) == 2
            assert second[-1] == VehicleTelemetry(50, 80, 82)
            assert len(second) == 3
            unsub2()
            publish(hass, SOC_DESCRIPTORS[0], 90)
            await hass.async_block_till_done()
            assert len(second) == 3
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_state_before_registry_notification_cannot_read_an_entity_moved_to_another_vehicle(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            entity = publish(hass, SOC_DESCRIPTORS[0], 50)
            source = BavarianTelemetrySource(hass, "car")
            values = []
            unsub = source.subscribe(values.append)
            hass.data[er.DATA_REGISTRY].entities[entity].device_id = "other-car"
            hass.states.async_set(entity, 85, dict(hass.states.get(entity).attributes))
            await hass.async_block_till_done()
            assert values == [VehicleTelemetry()]
            unsub()
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_entity_renames_and_new_optional_fields_rebind_without_other_vehicle_data(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            old = publish(hass, SOC_DESCRIPTORS[0], 40)
            source = BavarianTelemetrySource(hass, "car")
            values = []
            unsub = source.subscribe(values.append)
            registry = hass.data[er.DATA_REGISTRY]
            del registry.entities[old]
            new = publish(hass, SOC_DESCRIPTORS[0], 41, entity_id="sensor.renamed")
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": new, "old_entity_id": old})
            await hass.async_block_till_done()
            assert values[-1].soc_percent == 41
            publish(hass, SOC_DESCRIPTORS[0], 70, entity_id=new)
            await hass.async_block_till_done()
            assert values[-1].soc_percent == 70
            limit = publish(hass, LIMIT_DESCRIPTOR, 75)
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "create", "entity_id": limit})
            await hass.async_block_till_done()
            assert values[-1].maximum_percent == 75
            before = len(values)
            publish(hass, SOC_DESCRIPTORS[0], 100, entity_id="sensor.other_car", vin="WBA98765432109876", device="other")
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": "sensor.other_car"})
            await hass.async_block_till_done()
            assert len(values) == before
            # A registry entry moved away from this device must not remain bound.
            registry.entities[new].device_id = "other"
            hass.bus.async_fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": new})
            await hass.async_block_till_done()
            assert values[-1].soc_percent is None
            unsub()
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_late_device_registration_binds_once_and_replacement_fails_closed(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers=set())
            source = BavarianTelemetrySource(hass, "car")
            assert not source.available
            assert source.source_key == "bavariandata:car"
            values = []
            unsub = source.subscribe(values.append)
            publish(hass, SOC_DESCRIPTORS[0], 50)
            hass.data[dr.DATA_REGISTRY] = vehicle_registry()
            hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "car", "action": "update"})
            await hass.async_block_till_done()
            assert source.available
            assert values[-1].soc_percent == 50
            key = source.source_key
            hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
            publish(hass, SOC_DESCRIPTORS[0], 95)
            await hass.async_block_till_done()
            assert not source.available
            assert source.source_key == key
            assert source.snapshot == values[-1] == VehicleTelemetry()
            hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "car", "action": "update"})
            await hass.async_block_till_done()
            assert values[-1] == VehicleTelemetry()
            unsub()
        finally:
            await hass.async_stop()
    asyncio.run(run())


def test_persisted_vehicle_identity_survives_missing_registry_but_rejects_replacement(tmp_path):
    async def run():
        hass = local_hass(tmp_path)
        try:
            identity = provider_identity(hass, "bavariandata", "car")
            assert identity is not None
            assert provider_identity(hass, "none", "car") is None
            hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers=set())
            assert provider_identity(hass, "bavariandata", "car") is None
            assert telemetry_capabilities(hass, "car") == frozenset()
            options = {
                "vehicle_provider": "bavariandata", "vehicle_device": "car", "vehicle_source_identity": identity,
                "timed_vehicle_provider": "profile", "charge_loss_vehicle_provider": "profile",
            }
            sources = [resolver(hass, options) for resolver in (resolve_telemetry_source, resolve_departure_source, resolve_history_source)]
            assert all(source is not None and not source.available for source in sources)
            assert sources[0].source_key == sources[2].source_key == identity
            history_changes = []
            unsubscribe = sources[2].subscribe(lambda: history_changes.append(sources[2].available))
            hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "another-car", "action": "update"})
            await hass.async_block_till_done()
            assert history_changes == []
            hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
            assert all(not source.available for source in sources)
            hass.data[dr.DATA_REGISTRY] = vehicle_registry()
            assert all(source.available for source in sources)
            hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "car", "action": "update"})
            await hass.async_block_till_done()
            assert history_changes == [True]
            hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
            assert all(not source.available for source in sources)
            unsubscribe()
        finally:
            await hass.async_stop()
    asyncio.run(run())
