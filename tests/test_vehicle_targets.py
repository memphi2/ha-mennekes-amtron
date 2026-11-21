"""Each vehicle keeps its own charge target across vehicle changes."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mennekes_amtron_rtu.vehicle_soc import VehicleSocObserver
from custom_components.mennekes_amtron_rtu.vehicle_targets import (
    MAX_TARGETS,
    VehicleTargetMemory,
    VehicleTargets,
)
from tests.test_vehicle_soc import Control, Coordinator


def test_targets_keep_the_newest_vehicles_and_reject_invalid_state():
    targets = VehicleTargets()
    for index in range(MAX_TARGETS + 2):
        assert targets.remember(f"car{index}", 80)
    assert not targets.remember(f"car{MAX_TARGETS + 1}", 80)
    assert len(targets.targets) == MAX_TARGETS
    assert "car0" not in targets.targets
    assert "car1" not in targets.targets
    assert VehicleTargets.restore(targets.snapshot()).targets == targets.targets


@pytest.mark.parametrize("raw", [
    None, {}, {"targets": []}, {"targets": {"car": 101}}, {"targets": {"car": True}},
    {"targets": {"": 80}}, {"targets": {"car": float("nan")}}, {"targets": {}, "extra": 1},
])
def test_invalid_saved_targets_are_rejected(raw):
    with pytest.raises(ValueError, match="vehicle targets"):
        VehicleTargets.restore(raw)


async def _observer(hass, control, coordinator, soc_entity, memory):
    observer = VehicleSocObserver(hass, control, coordinator, soc_entity, "number.limit", memory=memory)
    await observer.async_start()
    await hass.async_block_till_done()
    return observer


def test_a_reselected_vehicle_gets_its_target_back_and_others_none(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator = Control(), Coordinator()
        for car in ("sensor.first", "sensor.second"):
            hass.states.async_set(car, "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        memory = VehicleTargetMemory(hass, "entry", "serial")
        try:
            first = await _observer(hass, control, coordinator, "sensor.first", memory)
            await first.async_set_target(70)
            await hass.async_block_till_done()
            await first.async_stop()
            # Binding another vehicle resets the goal; it had no target yet.
            second = await _observer(hass, control, coordinator, "sensor.second", memory)
            assert control.soc_goal.status == "disabled"
            await second.async_stop()
            # An unknown vehicle has no source and therefore no target at all.
            control.soc_goal.configure_source(None)
            reloaded = VehicleTargetMemory(hass, "entry", "serial")
            await reloaded.async_load()
            first = await _observer(hass, control, coordinator, "sensor.first", reloaded)
            assert (control.soc_goal.target_percent, control.soc_goal.status) == (70, "active")
            # An explicit disable is remembered too and not undone later.
            await first.async_set_target(0)
            await hass.async_block_till_done()
            await first.async_stop()
            control.soc_goal.configure_source(None)
            first = await _observer(hass, control, coordinator, "sensor.first", reloaded)
            assert control.soc_goal.status == "disabled"
            await first.async_stop()
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_a_restored_target_is_capped_by_a_lower_vehicle_limit(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.first", "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        memory = VehicleTargetMemory(hass, "entry", "serial")
        try:
            first = await _observer(hass, control, coordinator, "sensor.first", memory)
            await first.async_set_target(85)
            # A limit that presses the target down does not change what is
            # remembered: the chosen value comes back with a higher limit.
            hass.states.async_set("number.limit", "80", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 80
            assert list(memory._targets.targets.values()) == [85]
            await first.async_stop()
            control.soc_goal.configure_source(None)
            first = await _observer(hass, control, coordinator, "sensor.first", memory)
            assert (control.soc_goal.target_percent, control.soc_goal.chosen_percent) == (80, 85)
            # Restoring under the pressing limit does not rewrite the memory.
            assert list(memory._targets.targets.values()) == [85]
            hass.states.async_set("number.limit", "95", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 85
            await first.async_stop()
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_storage_problems_only_cost_the_remembered_targets(tmp_path, monkeypatch, caplog):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        memory = VehicleTargetMemory(hass, "entry", "serial")
        try:
            memory._store.path.parent.mkdir(parents=True, exist_ok=True)
            memory._store.path.write_text("not json")
            await memory.async_load()
            assert memory.recall("car") is None
            assert "Vehicle charge target: ignoring saved targets" in caplog.text

            async def fail(_goal):
                raise OSError("disk full")

            monkeypatch.setattr(memory._store, "async_save", fail)
            assert memory.remember("car", 80)
            await memory.async_save()
            assert memory.recall("car") == 80
            assert "Vehicle charge target: not saved" in caplog.text
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_a_profile_keeps_its_target_when_its_source_description_changes(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator = Control(), Coordinator()
        for car in ("sensor.before", "sensor.after", "sensor.legacy"):
            hass.states.async_set(car, "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        memory = VehicleTargetMemory(hass, "entry", "serial")

        async def observer(entity, profile):
            created = VehicleSocObserver(
                hass, control, coordinator, entity, "number.limit", profile_id=profile, memory=memory,
            )
            await created.async_start()
            await hass.async_block_till_done()
            return created

        try:
            car = await observer("sensor.before", "car")
            await car.async_set_target(70)
            await hass.async_block_till_done()
            await car.async_stop()
            assert memory.recall("profile:car") == 70
            # The same profile, now described by another source, gets it back.
            car = await observer("sensor.after", "car")
            assert (control.soc_goal.target_percent, control.soc_goal.status) == (70, "active")
            await car.async_stop()
        finally:
            await hass.async_stop()

    asyncio.run(run())


class Binding:
    """A binding whose resting state the test switches."""

    def __init__(self):
        self.resting = False
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def set(self, resting):
        self.resting = resting
        for listener in tuple(self.listeners):
            listener()


def test_an_unconfirmed_car_neither_reaches_nor_keeps_a_reached_target(tmp_path, caplog):
    caplog.set_level("INFO")

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator, binding = Control(), Coordinator(), Binding()
        control.pause_requested = None
        hass.states.async_set("sensor.first", "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        observer = VehicleSocObserver(
            hass, control, coordinator, "sensor.first", "number.limit", binding=binding,
        )
        try:
            await observer.async_start()
            await observer.async_set_target(80)
            hass.states.async_set("sensor.first", "85", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            control.pause_requested = True
            # Another car plugs in: the reach and its stop pause end.
            binding.set(True)
            await hass.async_block_till_done()
            assert control.soc_goal.status == "active"
            control.async_end_soc_stop_pause.assert_awaited_once()
            assert "Vehicle charge target: reached target released" in caplog.text
            assert observer.latest_soc is None
            # The resting car's reports change nothing.
            hass.states.async_set("sensor.first", "95", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.status == "active"
            # A second rest finds nothing to release.
            binding.set(True)
            await hass.async_block_till_done()
            control.async_end_soc_stop_pause.assert_awaited_once()
            # The confirmed car's charge level counts again.
            binding.set(False)
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            # A rest reported twice in a row releases the reach once; whether
            # a pause is the target's own to end, the service decides.
            binding.set(True)
            binding.set(True)
            await hass.async_block_till_done()
            assert control.soc_goal.status == "active"
            assert control.async_end_soc_stop_pause.await_count == 2
            binding.set(False)
            await hass.async_block_till_done()
            # A failed release is only logged at debug level.
            control.async_set_soc_goal.side_effect = OSError("bus")
            binding.set(True)
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
        finally:
            await observer.async_stop()
            # A stopped observer ignores the binding.
            observer._binding_changed()
            await hass.async_stop()

    asyncio.run(run())


def test_a_start_with_an_unconfirmed_car_releases_a_reached_target(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator, binding = Control(), Coordinator(), Binding()
        control.pause_requested = True
        source = "sensor.first|number.limit"
        control.soc_goal.configure_source(source)
        control.soc_goal.set_target(80, 85, 90)
        binding.resting = True
        hass.states.async_set("sensor.first", "85", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        observer = VehicleSocObserver(
            hass, control, coordinator, "sensor.first", "number.limit", binding=binding,
        )
        try:
            await observer.async_start()
            await hass.async_block_till_done()
            assert control.soc_goal.status == "active"
            control.async_end_soc_stop_pause.assert_awaited_once()
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_a_release_keeps_a_chosen_value_that_the_limit_fell_onto(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator, binding = Control(), Coordinator(), Binding()
        control.pause_requested = False
        hass.states.async_set("sensor.first", "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        memory = VehicleTargetMemory(hass, "entry", "serial")
        observer = VehicleSocObserver(
            hass, control, coordinator, "sensor.first", "number.limit", binding=binding, memory=memory,
        )
        try:
            await observer.async_start()
            await hass.async_block_till_done()
            await observer.async_set_target(80)
            hass.states.async_set("number.limit", "80", {"unit_of_measurement": "%"})
            hass.states.async_set("sensor.first", "80", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            # The binding rests: the reach is released by setting the target again.
            binding.set(True)
            await hass.async_block_till_done()
            binding.set(False)
            hass.states.async_set("number.limit", "100", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 80
            assert list(memory._targets.targets.values()) == [80]
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_a_restore_at_a_limit_equal_to_the_value_keeps_the_value(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.first", "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        memory = VehicleTargetMemory(hass, "entry", "serial")
        try:
            first = await _observer(hass, control, coordinator, "sensor.first", memory)
            await first.async_set_target(80)
            await first.async_stop()
            control.soc_goal.configure_source(None)
            hass.states.async_set("number.limit", "80", {"unit_of_measurement": "%"})
            first = await _observer(hass, control, coordinator, "sensor.first", memory)
            assert (control.soc_goal.target_percent, control.soc_goal.chosen_percent) == (80, 80)
            assert list(memory._targets.targets.values()) == [80]
            await first.async_stop()
        finally:
            await hass.async_stop()

    asyncio.run(run())


def test_only_a_value_picked_by_hand_at_the_top_follows_the_limit(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config.config_dir = str(tmp_path)
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.first", "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.limit", "90", {"unit_of_measurement": "%"})
        memory = VehicleTargetMemory(hass, "entry", "serial")
        try:
            first = await _observer(hass, control, coordinator, "sensor.first", memory)
            await first.async_set_target(90)
            assert (control.soc_goal.target_percent, control.soc_goal.chosen_percent) == (90, 100)
            hass.states.async_set("number.limit", "95", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 95
            # The same number set by the integration itself is that number.
            await first.async_set_target(95, automatic=True)
            assert control.soc_goal.chosen_percent == 95
            await first.async_stop()
        finally:
            await hass.async_stop()

    asyncio.run(run())
