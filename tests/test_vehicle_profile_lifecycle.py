"""Real HA profile changes rebind one target without starting the wallbox."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.timed_goal import TimedGoal
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes
from tests.test_soc_goal_ha_lifecycle import _entry
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
                await hass.services.async_call("select", "select_option", {
                    "entity_id": select, "option": "Second car",
                }, blocking=True)
                await hass.async_block_till_done()
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
                assert entry.runtime_data.control.is_paused is True
                assert current_writes(transport) == writes
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
