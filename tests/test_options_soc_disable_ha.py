"""Removing the SoC feature saves and resets its runtime through the actual UI."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.timed_goal import TimedGoal
from tests.fakes import FakeModbusClient, device_bank
from tests.options_flow_helpers import async_options
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes
from tests.test_soc_goal_ha_lifecycle import _entry
from tests.test_vehicle_profiles import A, B, profile_options


def test_remove_percentage_feature_clears_target_and_trip_without_resuming(tmp_path):
    async def run():
        transport = FakeModbusClient(device_bank(charging_current=8))
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                hass.states.async_set("sensor.car_soc", "20", {"unit_of_measurement": "%"})
                hass.states.async_set("number.car_maximum", "80", {"unit_of_measurement": "%"})
                entry = await _entry(hass)
                options = profile_options(provider="none", feature={
                    "vehicle_soc_entity": "sensor.car_soc",
                    "vehicle_charge_limit_entity": "number.car_maximum",
                    "timed_goal_enabled": True,
                    "timed_capacity_kwh": 80,
                })
                options.update({
                    "control_mode": "master", "scan_interval_seconds": 300,
                    "measurement_interval_seconds": 300, "state_change_interval_seconds": 0,
                })
                second_profile = deepcopy(options["vehicle_profiles"][B])
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                target = registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_soc_goal")
                pause = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused")
                assert target is not None
                assert pause is not None
                entry.runtime_data.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call("switch", "turn_on", {"entity_id": pause}, blocking=True)
                await entry.runtime_data.coordinator.async_refresh()
                await hass.services.async_call("number", "set_value", {"entity_id": target, "value": 75}, blocking=True)
                await hass.async_block_till_done()
                trip = TimedGoal()
                trip.set(dt_util.utcnow() + timedelta(hours=8))
                store = TimedGoalStore(hass, entry.entry_id, SERIAL_NUMBER)
                await store.async_save(trip)
                before = current_writes(transport)

                # Opening the car guides through its settings, name first.
                battery = (
                    "vehicle", "vehicle_profile_open", {"vehicle_profile_id": A},
                    {"vehicle_profile_name": "Car A", "vehicle_provider": "none"},
                )
                result = await async_options(hass, entry, *battery, "vehicle_disable", "wizard_finish")

                assert result["type"] == "create_entry"
                assert entry.options["active_vehicle_profile"] == A
                assert entry.options["vehicle_profiles"][B] == second_profile
                assert entry.options["vehicle_profiles"][A]["options"] == {
                    "timed_capacity_kwh": 80, "timed_goal_enabled": False,
                }
                assert entry.runtime_data.control.soc_goal.target_percent == 0
                assert entry.runtime_data.control.soc_goal.source is None
                assert entry.runtime_data.control.soc_goal.maximum_percent is None
                assert entry.runtime_data.timed_goal is None
                assert (await store.async_load()).deadline is None
                assert entry.runtime_data.control.is_paused is True
                assert current_writes(transport) == before
                menu = await async_options(hass, entry, *battery)
                assert "vehicle_disable" not in menu["menu_options"]
                hass.config_entries.options.async_abort(menu["flow_id"])
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
