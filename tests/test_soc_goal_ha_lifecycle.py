"""Configure a real HA vehicle target, observe its cap, stop and restore it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.vehicle_soc_const import (
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_SOC_ENTITY,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes

SOC = "sensor.test_car_soc"
LIMIT = "number.test_car_charge_limit"


async def _entry(hass):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "serial"},
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {"port": "/dev/fake", "device_id": 50, "baudrate": 57600,
         "frame": "8n2", "search": "off"},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


async def _options(hass, entry, *, limit=False):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"control_mode": CONTROL_MODE_MASTER, "scan_interval_seconds": 300,
         "measurement_interval_seconds": 300, "current_limit": 16,
         "state_change_interval_seconds": 0, "phase_switch_grace_seconds": 0,
         CONF_VEHICLE_SOC_ENTITY: SOC,
         **({CONF_VEHICLE_CHARGE_LIMIT_ENTITY: LIMIT} if limit else {})},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()


def test_vehicle_target_is_optional_follows_car_cap_and_restores_stop(tmp_path: Path):
    async def run():
        transport = FakeModbusClient(device_bank(charging_current=8))
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
                hass.states.async_set(LIMIT, "80", {"unit_of_measurement": "%"})
                entry = await _entry(hass)
                registry = er.async_get(hass)
                assert registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_soc_goal") is None
                assert registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL_NUMBER}_soc_goal_status") is None

                await _options(hass, entry)
                runtime = entry.runtime_data
                target = registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_soc_goal")
                status = registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL_NUMBER}_soc_goal_status")
                pause = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused")
                assert target is not None
                assert status is not None
                assert pause is not None
                assert hass.states.get(target).attributes["max"] == 100
                assert hass.states.get(target).attributes["unit_of_measurement"] == "%"
                assert hass.states.get(status).state == "disabled"

                async def set_target(value):
                    await hass.services.async_call(
                        "number", "set_value", {"entity_id": target, "value": value}, blocking=True,
                    )
                    await hass.async_block_till_done()

                # Choosing or clearing a ceiling does not resume a deliberate pause.
                runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call("switch", "turn_on", {"entity_id": pause}, blocking=True)
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                paused_writes = current_writes(transport)
                await set_target(80)
                assert hass.states.get(status).state == "active"
                assert hass.states.get(pause).state == "on"
                await set_target(0)
                assert hass.states.get(status).state == "disabled"
                assert hass.states.get(pause).state == "on"
                assert current_writes(transport) == paused_writes

                # Charging resumes only through the user's separate switch command.
                runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call("switch", "turn_off", {"entity_id": pause}, blocking=True)
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(pause).state == "off"
                await set_target(80)
                charging_writes = current_writes(transport)
                hass.states.async_set(SOC, "unavailable", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert hass.states.get(status).state == "active"
                assert hass.states.get(pause).state == "off"
                assert current_writes(transport) == charging_writes

                # Adding a different source pairing resets the previous car target.
                hass.states.async_set(SOC, "65", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                await _options(hass, entry, limit=True)
                runtime = entry.runtime_data
                assert hass.states.get(status).state == "disabled"
                assert hass.states.get(target).attributes["max"] == 80
                await set_target(75)
                hass.states.async_set(LIMIT, "70", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert hass.states.get(target).state == "70.0"
                assert hass.states.get(target).attributes["max"] == 70
                hass.states.async_set(LIMIT, "90", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert hass.states.get(target).state == "70.0"
                assert hass.states.get(target).attributes["max"] == 90
                for missing in ("unavailable", "0"):
                    hass.states.async_set(LIMIT, missing, {"unit_of_measurement": "%"})
                    await hass.async_block_till_done()
                    assert hass.states.get(target).attributes["max"] == 90
                    assert hass.states.get(status).state == "active"
                assert current_writes(transport) == charging_writes

                # Lowering the vehicle cap below its present SoC requests a real pause.
                stopped = asyncio.Event()

                @callback
                def state_changed(event):
                    state = event.data["new_state"]
                    if state is not None and state.state == "on":
                        stopped.set()

                unsubscribe = async_track_state_change_event(hass, pause, state_changed)
                try:
                    runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                    hass.states.async_set(LIMIT, "60", {"unit_of_measurement": "%"})
                    await asyncio.wait_for(stopped.wait(), 15)
                    await hass.async_block_till_done()
                finally:
                    unsubscribe()
                assert hass.states.get(status).state == "reached"
                assert hass.states.get(target).state == "60.0"
                assert current_writes(transport)[-1] == 1

                # A cold goal owner must restore from disk without starting the car.
                before_reload = current_writes(transport)
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.data[f"{DOMAIN}_write_states"].pop(entry.entry_id)
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                assert hass.states.get(status).state == "reached"
                assert hass.states.get(target).state == "60.0"
                assert hass.states.get(pause).state == "on"
                assert current_writes(transport) == before_reload
                await set_target(0)
                assert hass.states.get(status).state == "disabled"
                assert hass.states.get(pause).state == "on"
                assert current_writes(transport) == before_reload
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
