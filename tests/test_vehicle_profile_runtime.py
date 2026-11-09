"""Vehicle changes isolate goals, source observations and durable loss totals."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.charge_loss_observer import (
    ChargeLossObserver,
)
from custom_components.mennekes_amtron_rtu.device.charge_loss import LossStatistics
from custom_components.mennekes_amtron_rtu.loss_store import (
    LossStore,
    async_prune_profile_losses,
)
from custom_components.mennekes_amtron_rtu.select import async_setup_entry
from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    LEGACY_PROFILE_ID,
    activate_profile,
)
from custom_components.mennekes_amtron_rtu.vehicle_soc import VehicleSocObserver
from custom_components.mennekes_amtron_rtu.vehicle_sources import VehicleTelemetry
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect
from tests.test_charge_loss_observer import Coordinator as LossCoordinator
from tests.test_timed_goal import setup_observer
from tests.test_vehicle_soc import Control, Coordinator

A = "a" * 32
B = "b" * 32


def catalog(*, selected=A):
    return {
        "vehicle_profiles": {
            A: {"name": "First car", "provider": "none", "device_id": "", "options": {
                "vehicle_soc_entity": "sensor.first_soc",
                "charge_loss_source": "sensor", "vehicle_last_charge_energy_entity": "sensor.first_energy",
            }},
            B: {"name": "Second car", "provider": "none", "device_id": "", "options": {
                "vehicle_soc_entity": "sensor.second_soc",
                "charge_loss_source": "sensor", "vehicle_last_charge_energy_entity": "sensor.second_energy",
            }},
        },
        "active_vehicle_profile": selected,
        "vehicle_profile_selected_at": 1000.0,
    }


class Telemetry:
    source_key = "provider:device:hashed-identity"
    available = True

    def __init__(self):
        self.snapshot = VehicleTelemetry(20, None, 80)
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def publish(self, soc, maximum, capacity=80):
        self.snapshot = VehicleTelemetry(soc, maximum, capacity)
        for listener in tuple(self.listeners):
            listener(self.snapshot)


def test_profile_telemetry_preserves_brief_stop_and_requires_first_maximum(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        source, control = Telemetry(), Control()
        observer = VehicleSocObserver(
            hass, control, Coordinator(), None, telemetry=source,
            use_vehicle_soc=True, use_vehicle_limit=True, profile_id=A,
        )
        try:
            await observer.async_start()
            with pytest.raises(ServiceValidationError) as error:
                await observer.async_set_target(80)
            assert error.value.translation_key == "soc_goal_limit_unavailable"
            source.publish(20, 90)
            await hass.async_block_till_done()
            await observer.async_set_target(80)
            source.publish(85, 90)
            source.publish(30, 90)
            await hass.async_block_till_done()
            assert control.soc_goal.stop_requested
            assert observer.latest_soc == 30
            assert observer.maximum == 90
            assert control.soc_goal.source.startswith(f"profile:{A}:vehicle:")
        finally:
            await observer.async_stop()
            assert not source.listeners
            await hass.async_stop()

    asyncio.run(run())


def test_profile_change_resets_target_but_legacy_adoption_preserves_it(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "20", {"unit_of_measurement": "%"})
        control.soc_goal.configure_source("sensor.car|")
        control.soc_goal.set_target(80, 20)
        legacy = VehicleSocObserver(hass, control, coordinator, "sensor.car", profile_id=LEGACY_PROFILE_ID)
        try:
            await legacy.async_start()
            assert control.soc_goal.target_percent == 80
            await legacy.async_stop()
            replacement = VehicleSocObserver(hass, control, coordinator, "sensor.car", profile_id=A)
            await replacement.async_start()
            assert control.soc_goal.target_percent == 0
            assert not control.soc_goal.stop_requested
            await replacement.async_stop()
        finally:
            await legacy.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_profile_telemetry_and_manual_maximum_remain_independent(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        source, control = Telemetry(), Control()
        hass.states.async_set("number.car_max", "70", {"unit_of_measurement": "%"})
        observer = VehicleSocObserver(
            hass, control, Coordinator(), None, "number.car_max",
            telemetry=source, use_vehicle_soc=True, profile_id=A,
        )
        try:
            await observer.async_start()
            await observer.async_set_target(60)
            source.publish(20, 95)
            await hass.async_block_till_done()
            assert observer.maximum == 70
            hass.states.async_set("number.car_max", "15", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 15
            assert control.soc_goal.status == "reached"
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_manual_profile_selection_is_available_without_write_permission():
    async def run():
        entry = FakeConfigEntry(options=catalog())
        hass, entry, transport = await build_runtime(entry=entry, read_only=True)
        entities = {}
        await async_setup_entry(hass, entry, lambda items: entities.update(collect(items)))
        try:
            assert set(entities) == {"active_vehicle"}
            selector = entities["active_vehicle"]
            selector.hass = hass
            assert selector.available
            assert selector.current_option == "First car"
            assert selector.options == ["none", "First car", "Second car"]
            writes = list(transport.writes)
            await selector.async_select_option("Second car")
            assert entry.options["active_vehicle_profile"] == B
            assert entry.options["timed_goal_reset_pending"] is True
            assert selector.current_option == "Second car"
            await selector.async_select_option("none")
            assert selector.current_option == "none"
            assert transport.writes == writes
            with pytest.raises(ServiceValidationError):
                await selector.async_select_option("Missing car")
        finally:
            await entry.runtime_data.control.async_shutdown()
            await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_loss_storage_is_separate_and_prunes_only_deleted_profile_files(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        first, second = (LossStore(hass, "entry", "wallbox", profile_id=key) for key in (A, B))
        legacy = LossStore(hass, "entry", "wallbox", profile_id=LEGACY_PROFILE_ID)
        other = LossStore(hass, "other", "wallbox", profile_id=B)
        try:
            for store, source in ((first, "first"), (second, "second"), (legacy, "legacy"), (other, "other")):
                model = LossStatistics()
                model.configure_source(source)
                await store.async_save(model)
            assert (await first.async_load()).source == "first"
            assert (await second.async_load()).source == "second"
            assert legacy.path == LossStore(hass, "entry", "wallbox").path
            unrelated = first.path.with_name(first.path.name + ".unrelated")
            unrelated.write_text("unrelated")
            await async_prune_profile_losses(hass, "entry", [A])
            assert first.path.exists()
            assert legacy.path.exists()
            assert other.path.exists()
            assert unrelated.exists()
            assert not second.path.exists()
            with pytest.raises(ValueError, match="invalid vehicle profile storage key"):
                LossStore(hass, "entry", "wallbox", profile_id="../elsewhere")
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_switching_profiles_retains_loss_totals_without_reassigning_previous_charge(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        coordinator = LossCoordinator()
        entry = SimpleNamespace(entry_id="entry", options=catalog(), runtime_data=SimpleNamespace(
            coordinator=coordinator, identity=SimpleNamespace(serial_number="123", layout_version=310),
        ))
        hass.states.async_set("sensor.first_energy", "8", {"unit_of_measurement": "kWh"})
        hass.states.async_set("sensor.second_energy", "5", {"unit_of_measurement": "kWh"})
        first = ChargeLossObserver(hass, entry)
        try:
            await first.async_start()
            coordinator.complete(at=1200)
            await hass.async_block_till_done()
            await first.async_assign()
            assert first.statistics.average_loss_percent == pytest.approx(20)
            await first.async_stop()
            monkeypatch.setattr("custom_components.mennekes_amtron_rtu.vehicle_profiles.time.time", lambda: 1300)
            entry.options = activate_profile(entry.options, B)
            second = ChargeLossObserver(hass, entry)
            await second.async_start()
            assert not second.can_assign
            assert second.statistics.sample_count == 0
            coordinator.complete(at=1500)
            await hass.async_block_till_done()
            await second.async_assign()
            assert second.statistics.average_loss_percent == pytest.approx(50)
            await second.async_stop()
            monkeypatch.setattr("custom_components.mennekes_amtron_rtu.vehicle_profiles.time.time", lambda: 1600)
            entry.options = activate_profile(entry.options, A)
            entry.options["vehicle_profiles"][A]["options"]["charge_loss_auto_match"] = True
            restored = ChargeLossObserver(hass, entry)
            await restored.async_start()
            assert restored.statistics.average_loss_percent == pytest.approx(20)
            assert restored.statistics.last_loss_percent == pytest.approx(20)
            assert not restored.can_assign
            hass.states.async_set("sensor.first_energy", "9", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert restored.statistics.last_loss_percent == pytest.approx(20)
            assert restored.statistics.sample_count == 1
            await restored.async_stop()
        finally:
            await first.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_departure_keeps_captured_profile_options_and_uses_optional_vehicle_capacity(monkeypatch):
    observer, _control, _hass = setup_observer(monkeypatch)
    source = Telemetry()
    observer._runtime.vehicle_telemetry = source
    observer._options["timed_use_vehicle_capacity"] = True
    assert observer._capacity() == 80
    source.publish(20, 90, None)
    assert observer._capacity() is None
    # A concurrently selected next car cannot change the old worker's sources.
    observer._entry.options = {"timed_capacity_kwh": 120}
    assert observer._capacity() is None
    observer._options["timed_use_vehicle_capacity"] = False
    assert observer._capacity() == 80
