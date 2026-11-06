"""Optional estimates stay truthful, compact and independent of write permission."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory, Platform
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu import _entry_platforms, button, sensor
from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    LOSS_SENSOR_KEYS,
)
from custom_components.mennekes_amtron_rtu.cost_observer import LastChargingCost
from custom_components.mennekes_amtron_rtu.device.charge_loss import LossStatistics
from custom_components.mennekes_amtron_rtu.device.charging_cost import CostResult
from custom_components.mennekes_amtron_rtu.estimate_sensor import AmtronEstimateSensor
from custom_components.mennekes_amtron_rtu.sensor_descriptions import (
    ESTIMATE_SENSOR_DESCRIPTIONS,
)
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect
from tests.test_charging_session import complete


def loss_observer(mode="sensor"):
    stats = LossStatistics()
    stats.configure_source("sensor.car_energy")
    return SimpleNamespace(
        statistics=stats, available=True, mode=mode,
        can_assign=True, async_assign=AsyncMock(),
    )


def cost_observer():
    return SimpleNamespace(available=True, current=None, last=LastChargingCost())


def estimate_entities(entry):
    return {
        item.key: AmtronEstimateSensor(entry, entry.runtime_data.coordinator, item)
        for item in ESTIMATE_SENSOR_DESCRIPTIONS
    }


@pytest.mark.parametrize("loss_enabled", [False, True])
@pytest.mark.parametrize("cost_enabled", [False, True])
def test_only_configured_estimate_groups_are_added(loss_enabled, cost_enabled):
    async def run():
        hass, entry, _transport = await build_runtime(read_only=True)
        runtime = entry.runtime_data
        runtime.charge_loss = loss_observer() if loss_enabled else None
        runtime.charging_cost = cost_observer() if cost_enabled else None
        entities = {}
        await sensor.async_setup_entry(hass, entry, lambda rows: entities.update(collect(rows)))
        for description in ESTIMATE_SENSOR_DESCRIPTIONS:
            assert (description.key in entities) is (loss_enabled if description.key in LOSS_SENSOR_KEYS else cost_enabled)
            if description.key in entities:
                entity = entities[description.key]
                assert entity.state_class is None
                assert not entity.should_poll
                assert entity.unique_id == f"ABC123456789_{description.key}"
        await runtime.client.async_close()

    asyncio.run(run())


def test_loss_entities_round_values_and_publish_no_per_poll_timestamps():
    async def run():
        _hass, entry, _transport = await build_runtime(read_only=True)
        observer = loss_observer()
        entry.runtime_data.charge_loss = observer
        entities = estimate_entities(entry)
        last, average = (entities[key] for key in LOSS_SENSOR_KEYS)
        assert last.available
        assert average.available
        assert last.native_value is None
        assert average.native_value is None
        assert last.extra_state_attributes == {"sample_count": 0}
        first = complete().latest
        observer.statistics.apply(first, 9.123, association="manual")
        assert last.native_value == average.native_value == 8.8
        assert last.native_unit_of_measurement == "%"
        assert last.extra_state_attributes == {"sample_count": 1, "association": "manual"}
        assert average.extra_state_attributes == {"sample_count": 1}
        stable = last.native_value, last.extra_state_attributes
        observer.statistics.apply(first, 9.124, association="manual")
        assert (last.native_value, last.extra_state_attributes) == stable
        second = replace(complete(offset=100).latest, wallbox_kwh=20)
        observer.statistics.apply(second, 16.789, association="history")
        assert last.native_value == 16.1
        assert average.native_value == 13.6
        assert last.extra_state_attributes == {"sample_count": 2, "association": "history"}
        observer.statistics.observe_session(complete(offset=200).latest)
        assert last.native_value is None
        assert average.native_value == 13.6
        assert last.extra_state_attributes == {"sample_count": 2}
        observer.available = False
        assert not last.available
        assert not average.available
        entry.runtime_data.charge_loss = None
        assert not last.available
        assert not average.available
        assert last.native_value is None
        assert average.native_value is None
        assert last.extra_state_attributes is None
        assert average.extra_state_attributes is None
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_cost_entities_distinguish_unknown_incomplete_zero_and_negative_cost():
    async def run():
        _hass, entry, _transport = await build_runtime(read_only=True)
        observer = cost_observer()
        entry.runtime_data.charging_cost = observer
        entities = estimate_entities(entry)
        current = entities["current_estimated_charging_cost"]
        last = entities["last_estimated_charging_cost"]
        assert current.available
        assert last.available
        assert current.native_value is None
        assert last.native_value is None
        assert current.extra_state_attributes is None
        assert last.extra_state_attributes is None
        observer.current = CostResult(3.4567, 0, 12.34567, True)
        observer.last.result = CostResult(-1.2345, 0, 20.00003, True)
        assert current.native_value == 3.46
        assert last.native_value == -1.23
        assert current.native_unit_of_measurement == last.native_unit_of_measurement == "EUR"
        assert current.extra_state_attributes == {"complete": True}
        assert last.extra_state_attributes == {"complete": True, "energy_kwh": 20, "unpriced_kwh": 0}
        observer.current = CostResult(3.4, 2.123, 12.34567, False)
        assert current.native_value is None
        assert current.extra_state_attributes == {"complete": False}
        observer.current = CostResult(0, 0, 2, True)
        assert current.native_value == 0
        observer.available = False
        assert not current.available
        assert not last.available
        entry.runtime_data.charging_cost = None
        assert not current.available
        assert not last.available
        assert current.native_value is None
        assert last.native_value is None
        assert current.extra_state_attributes is None
        assert last.extra_state_attributes is None
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("mode", [None, "sensor", "bavariandata"])
def test_assign_button_is_optional_and_never_requires_physical_write_permission(read_only, remote, mode):
    async def run():
        hass, entry, transport = await build_runtime(read_only=read_only)
        if remote:
            entry.data = {**entry.data, "server_url": "http://service:8765"}
        entry.options = {**entry.options, CONF_CHARGE_LOSS_SOURCE: mode or "disabled"}
        observer = loss_observer(mode) if mode else None
        entry.runtime_data.charge_loss = observer
        entities = {}
        await button.async_setup_entry(hass, entry, lambda rows: entities.update(collect(rows)))
        assert ("assign_vehicle_charge" in entities) is (mode == "sensor")
        assert ("recover_from_error" in entities) is (not read_only or remote)
        assert ("cancel_pending_change" in entities) is (not read_only or remote)
        assert ("restart" in entities) is (not read_only or remote)
        if read_only and not remote:
            assert (Platform.BUTTON in _entry_platforms(entry)) is (mode == "sensor")
        if mode == "sensor":
            assign = entities["assign_vehicle_charge"]
            assert assign.entity_category == EntityCategory.CONFIG
            assert assign.available
            await assign.async_press()
            observer.async_assign.assert_awaited_once()
            assert not transport.writes
            observer.can_assign = False
            assert not assign.available
            entry.runtime_data.charge_loss = None
            assert not assign.available
            with pytest.raises(ServiceValidationError) as raised:
                await assign.async_press()
            assert raised.value.translation_key == "loss_unavailable"
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_read_only_without_optional_features_keeps_original_platform_list():
    assert _entry_platforms(FakeConfigEntry()) == [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT]
