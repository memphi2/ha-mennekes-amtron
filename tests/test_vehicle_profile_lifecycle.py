"""Real HA profile changes rebind one target without starting the wallbox."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.vehicle_profiles import activate_profile
from custom_components.mennekes_amtron_rtu.vehicle_targets import VehicleTargetMemory
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes
from tests.test_soc_goal_ha_lifecycle import _entry
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A, B, catalog


def test_real_profile_select_rebinds_sources_clears_old_trip_and_preserves_pause(tmp_path):
    async def run():
        transport = FakeModbusClient(device_bank(charging_current=0))
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                for entity, value in (
                    ("sensor.first_soc", 20), ("sensor.second_soc", 30),
                    ("number.first_max", 80), ("number.second_max", 60),
                ):
                    hass.states.async_set(entity, str(value), {"unit_of_measurement": "%"})
                entry = await _entry(hass)
                options = catalog()
                options.update({
                    "control_mode": "master", "scan_interval_seconds": 300,
                    "measurement_interval_seconds": 300, "current_limit": 16,
                    "state_change_interval_seconds": 0, "phase_switch_grace_seconds": 0,
                })
                for key, maximum in ((A, "number.first_max"), (B, "number.second_max")):
                    values = options["vehicle_profiles"][key]["options"]
                    values.pop("charge_loss_source")
                    values.pop("vehicle_last_charge_energy_entity")
                    values.update({
                        "vehicle_charge_limit_entity": maximum,
                        "timed_goal_enabled": True, "timed_capacity_kwh": 80,
                    })
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                select = registry.async_get_entity_id("select", DOMAIN, f"{SERIAL_NUMBER}_active_vehicle")
                target = registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_soc_goal")
                assert select is not None
                assert target is not None
                assert hass.states.get(select).state == "First car"
                assert hass.states.get(target).attributes["max"] == 80
                pause = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused")
                assert pause is not None
                entry.runtime_data.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call("switch", "turn_on", {"entity_id": pause}, blocking=True)
                await entry.runtime_data.coordinator.async_refresh()
                await hass.services.async_call("number", "set_value", {"entity_id": target, "value": 75}, blocking=True)
                await hass.async_block_till_done()
                assert entry.runtime_data.control.is_paused is True
                old_trip = TimedGoal()
                old_trip.set(dt_util.utcnow() + timedelta(hours=8))
                await TimedGoalStore(hass, entry.entry_id, SERIAL_NUMBER).async_save(old_trip)
                writes = current_writes(transport)
                runtime = entry.runtime_data
                unavailable = []

                def watch(event):
                    state = event.data["new_state"]
                    if state is None or state.state == "unavailable":
                        unavailable.append(event.data["entity_id"])

                unsubscribe = hass.bus.async_listen("state_changed", watch)
                await hass.services.async_call("select", "select_option", {
                    "entity_id": select, "option": "Second car",
                }, blocking=True)
                await hass.async_block_till_done()
                # The switch is applied in place: no reload, no entity flicker.
                assert entry.runtime_data is runtime
                assert unavailable == [], unavailable
                assert entry.runtime_data.vehicle_profile_id == B
                assert "timed_goal_reset_pending" not in entry.options
                assert entry.options["active_vehicle_profile"] == B
                assert entry.runtime_data.control.soc_goal.target_percent == 0
                assert entry.runtime_data.timed_goal.goal.deadline is None
                assert entry.runtime_data.control.is_paused is True
                assert current_writes(transport) == writes
                assert hass.states.get(target).attributes["max"] == 60
                assert hass.states.get(select).state == "Second car"
                assert entry.runtime_data.vehicle_soc.latest_soc == 30
                hass.states.async_set("sensor.first_soc", "99", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert entry.runtime_data.vehicle_soc.latest_soc == 30
                await hass.services.async_call("select", "select_option", {
                    "entity_id": select, "option": "none",
                }, blocking=True)
                await hass.async_block_till_done()
                assert entry.runtime_data.control.soc_goal.source is None
                assert entry.runtime_data.timed_goal is None
                assert entry.runtime_data is runtime
                # Vehicle entities stay, unavailable without a selected car.
                assert {entity.split(".")[1] for entity in unavailable} == {
                    "mennekes_amtron_vehicle_charge_target",
                    "mennekes_amtron_vehicle_charge_target_status",
                    "mennekes_amtron_timed_charging_status",
                    "mennekes_amtron_next_departure",
                    "mennekes_amtron_clear_departure_time",
                }
                assert hass.states.get(target).state == "unavailable"
                unsubscribe()
                assert entry.runtime_data.control.is_paused is True
                assert current_writes(transport) == writes
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))


def test_real_automatic_selection_reloads_goals_and_respects_manual_override(tmp_path, monkeypatch):
    async def run():
        transport = FakeModbusClient(device_bank(charging_current=8))
        monkeypatch.setattr("custom_components.mennekes_amtron_rtu.vehicle_detection.SETTLE_SECONDS", 0.02)
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                for name, plugged in (("first", "on"), ("second", "off")):
                    hass.states.async_set(f"sensor.{name}_soc", "20", {"unit_of_measurement": "%"})
                    hass.states.async_set(f"binary_sensor.{name}_plug", plugged)
                    hass.states.async_set(f"device_tracker.{name}", "home")
                entry = await _entry(hass)
                options = detection_catalog()
                options.update({"control_mode": "master", "scan_interval_seconds": 300,
                                "measurement_interval_seconds": 300, "state_change_interval_seconds": 0})
                for profile in options["vehicle_profiles"].values():
                    profile["options"].pop("charge_loss_source")
                    profile["options"].pop("vehicle_last_charge_energy_entity")
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                selector = registry.async_get_entity_id("select", DOMAIN, f"{SERIAL_NUMBER}_active_vehicle")
                target = registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_soc_goal")
                pause = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused")
                assert selector is not None
                assert target is not None
                assert pause is not None
                assert hass.states.get(selector).state == "automatic"
                assert hass.states.get(selector).attributes["active_vehicle"] == "First car"
                entry.runtime_data.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call("switch", "turn_on", {"entity_id": pause}, blocking=True)
                await entry.runtime_data.coordinator.async_refresh()
                await hass.services.async_call("number", "set_value", {"entity_id": target, "value": 75}, blocking=True)
                writes = current_writes(transport)
                # Two plugged cars cannot inherit one another's charge target.
                hass.states.async_set("binary_sensor.second_plug", "on")
                await hass.async_block_till_done()
                assert entry.options["active_vehicle_profile"] is None
                assert entry.runtime_data.control.soc_goal.target_percent == 0
                assert entry.runtime_data.control.is_paused
                hass.states.async_set("binary_sensor.first_plug", "off")
                await asyncio.sleep(0.05)
                await hass.async_block_till_done()
                assert entry.options["active_vehicle_profile"] == B
                assert hass.states.get(selector).attributes["active_vehicle"] == "Second car"
                assert entry.runtime_data.control.is_paused
                assert current_writes(transport) == writes
                await hass.services.async_call("select", "select_option", {
                    "entity_id": selector, "option": "First car",
                }, blocking=True)
                await hass.async_block_till_done()
                hass.states.async_set("binary_sensor.second_plug", "on")
                await hass.async_block_till_done()
                assert entry.options["vehicle_auto_selection"] is False
                assert entry.options["active_vehicle_profile"] == A
                assert entry.runtime_data.vehicle_detection is None
                assert current_writes(transport) == writes
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))


def test_vehicle_switch_runs_in_place_and_reloads_only_on_failure_or_other_changes(tmp_path, monkeypatch):
    async def run():
        transport = FakeModbusClient(device_bank(charging_current=8))
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                for name in ("first", "second"):
                    hass.states.async_set(f"sensor.{name}_soc", "20", {"unit_of_measurement": "%"})
                    hass.states.async_set(f"binary_sensor.{name}_plug", "off")
                    hass.states.async_set(f"device_tracker.{name}", "home")
                entry = await _entry(hass)
                options = detection_catalog()
                options.update({"control_mode": "master", "scan_interval_seconds": 300,
                                "measurement_interval_seconds": 300, "vehicle_auto_selection": False})
                for profile in options["vehicle_profiles"].values():
                    profile["options"].pop("charge_loss_source")
                    profile["options"].pop("vehicle_last_charge_energy_entity")
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                assert runtime.vehicle_detection is None
                selector = er.async_get(hass).async_get_entity_id(
                    "select", DOMAIN, f"{SERIAL_NUMBER}_active_vehicle",
                )
                # Re-enabling automatic selection starts its detector in place.
                await hass.services.async_call("select", "select_option", {
                    "entity_id": selector, "option": "automatic",
                }, blocking=True)
                await hass.async_block_till_done()
                assert entry.runtime_data is runtime
                assert runtime.vehicle_detection is not None
                # An unchanged save is neither a switch nor a reload.
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, "vehicle_profile_selected_at": entry.options.get("vehicle_profile_selected_at")},
                )
                await hass.async_block_till_done()
                assert entry.runtime_data is runtime

                failures = [OSError("storage failed")]
                original_load = VehicleTargetMemory.async_load

                async def broken_start(self):
                    if failures:
                        raise failures.pop()
                    await original_load(self)

                # A failed switch falls back to a reload, which retries setup.
                monkeypatch.setattr(
                    "custom_components.mennekes_amtron_rtu.VehicleTargetMemory.async_load", broken_start,
                )
                await hass.services.async_call("select", "select_option", {
                    "entity_id": selector, "option": "Second car",
                }, blocking=True)
                await hass.async_block_till_done()
                assert entry.runtime_data is not runtime
                assert entry.options["active_vehicle_profile"] == B
                assert not failures
                runtime = entry.runtime_data
                # Any other setting still reloads the entry.
                hass.config_entries.async_update_entry(entry, options={**entry.options, "scan_interval_seconds": 200})
                await hass.async_block_till_done()
                assert entry.runtime_data is not runtime
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))


def test_each_car_keeps_its_own_departure_across_switches(tmp_path):
    async def run():
        transport = FakeModbusClient(device_bank(charging_current=0))
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                for entity in ("sensor.first_soc", "sensor.second_soc"):
                    hass.states.async_set(entity, "20", {"unit_of_measurement": "%"})
                entry = await _entry(hass)
                options = catalog()
                options.update({"control_mode": "master", "scan_interval_seconds": 300})
                for key in (A, B):
                    values = options["vehicle_profiles"][key]["options"]
                    values.pop("charge_loss_source")
                    values.pop("vehicle_last_charge_energy_entity")
                    values.update({"timed_goal_enabled": True, "timed_capacity_kwh": 80})
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                select = registry.async_get_entity_id("select", DOMAIN, f"{SERIAL_NUMBER}_active_vehicle")
                departure = registry.async_get_entity_id("datetime", DOMAIN, f"{SERIAL_NUMBER}_timed_departure")
                assert select is not None
                assert departure is not None
                runtime = entry.runtime_data
                first = (dt_util.utcnow() + timedelta(hours=8)).replace(microsecond=0)
                second = first + timedelta(hours=2)

                async def choose(option):
                    await hass.services.async_call("select", "select_option", {
                        "entity_id": select, "option": option,
                    }, blocking=True)
                    await hass.async_block_till_done()
                    assert entry.runtime_data is runtime

                async def plan(value):
                    await hass.services.async_call("datetime", "set_value", {
                        "entity_id": departure, "datetime": value,
                    }, blocking=True)
                    await hass.async_block_till_done()

                def planned():
                    observer = entry.runtime_data.timed_goal
                    return observer.goal.deadline if observer is not None else "no car"

                await plan(first)
                assert planned() == first
                # Another car has no departure of its own.
                await choose("Second car")
                assert planned() is None
                await plan(second)
                # Each car gets its own departure back.
                await choose("First car")
                assert planned() == first
                await choose("none")
                assert planned() == "no car"
                await choose("Second car")
                assert planned() == second
                # A departure cleared for one car stays cleared for it.
                await hass.services.async_call("button", "press", {
                    "entity_id": registry.async_get_entity_id(
                        "button", DOMAIN, f"{SERIAL_NUMBER}_timed_clear_departure"),
                }, blocking=True)
                await choose("First car")
                await choose("Second car")
                assert planned() is None
                # The trip survives a reload that also selects another car.
                await choose("First car")
                assert planned() == first
                options = activate_profile(entry.options, B)
                options["scan_interval_seconds"] = 301
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                assert planned() is None
                await choose("First car")
                assert planned() == first
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
