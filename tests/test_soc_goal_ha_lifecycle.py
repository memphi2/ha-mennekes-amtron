"""Configure a real HA vehicle target, observe its cap, stop and restore it."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er

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
from tests.options_flow_helpers import async_options
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes

SOC = "sensor.test_car_soc"
LIMIT = "number.test_car_charge_limit"


def forget_current_write(hass, entry):
    """Let the next current write through the service's minimum write interval."""
    host = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]
    host._service.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)


async def settle(hass, entry):
    """Let the service apply queued writes and show them in Home Assistant."""
    service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service
    if pending := service.control._flush_task:
        await pending
    await service.refresh()
    if service.goal is not None and (worker := service.goal._task) is not None:
        await asyncio.wait_for(asyncio.shield(worker), 5)
    if pending := service.control._flush_task:
        await pending
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def vehicle_target(hass, waiting):
    """The vehicle target's status as the operation sensor shows it."""
    return hass.states.get(waiting).attributes.get("vehicle_target")


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
    await async_options(
        hass, entry, "wallbox", "wallbox_basic",
        {"control_mode": CONTROL_MODE_MASTER, "scan_interval_seconds": 300, "current_limit": 16},
    )
    await async_options(
        hass, entry, "wallbox", "wallbox_advanced",
        {"measurement_interval_seconds": 300, "state_change_interval_seconds": 0, "phase_switch_grace_seconds": 0},
    )
    steps = ["vehicle", "vehicle_settings", "vehicle_battery", "vehicle_soc", {CONF_VEHICLE_SOC_ENTITY: SOC, "use_vehicle_limit": limit}]
    if limit:
        steps.append({CONF_VEHICLE_CHARGE_LIMIT_ENTITY: LIMIT})
    await async_options(hass, entry, *steps)


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
                waiting = registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL_NUMBER}_operation_waiting")
                assert waiting is not None
                assert vehicle_target(hass, waiting) is None

                await _options(hass, entry)
                runtime = entry.runtime_data
                target = registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_soc_goal")
                pause = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused")
                assert target is not None
                assert pause is not None
                assert hass.states.get(target).attributes["max"] == 100
                assert hass.states.get(target).attributes["unit_of_measurement"] == "%"
                assert vehicle_target(hass, waiting) == "disabled"

                async def set_target(value):
                    await hass.services.async_call(
                        "number", "set_value", {"entity_id": target, "value": value}, blocking=True,
                    )
                    await hass.async_block_till_done()

                # Choosing or clearing a ceiling does not resume a deliberate pause.
                forget_current_write(hass, entry)
                await hass.services.async_call("switch", "turn_on", {"entity_id": pause}, blocking=True)
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                paused_writes = current_writes(transport)
                await set_target(80)
                assert vehicle_target(hass, waiting) == "active"
                assert hass.states.get(pause).state == "on"
                await set_target(0)
                assert vehicle_target(hass, waiting) == "disabled"
                assert hass.states.get(pause).state == "on"
                assert current_writes(transport) == paused_writes

                # Charging resumes only through the user's separate switch command.
                forget_current_write(hass, entry)
                await hass.services.async_call("switch", "turn_off", {"entity_id": pause}, blocking=True)
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(pause).state == "off"
                await set_target(80)
                charging_writes = current_writes(transport)
                hass.states.async_set(SOC, "unavailable", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert vehicle_target(hass, waiting) == "active"
                assert hass.states.get(pause).state == "off"
                assert current_writes(transport) == charging_writes

                # Adding a different source pairing resets the previous car target.
                hass.states.async_set(SOC, "65", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                await _options(hass, entry, limit=True)
                runtime = entry.runtime_data
                assert vehicle_target(hass, waiting) == "disabled"
                assert hass.states.get(target).attributes["max"] == 80
                await set_target(75)
                hass.states.async_set(LIMIT, "70", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert hass.states.get(target).state == "70.0"
                assert hass.states.get(target).attributes["max"] == 70
                # Clamped to the car's limit, the target now follows that limit.
                hass.states.async_set(LIMIT, "90", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert hass.states.get(target).state == "90.0"
                assert hass.states.get(target).attributes["max"] == 90
                for missing in ("unavailable", "0"):
                    hass.states.async_set(LIMIT, missing, {"unit_of_measurement": "%"})
                    await hass.async_block_till_done()
                    assert hass.states.get(target).attributes["max"] == 90
                    assert vehicle_target(hass, waiting) == "active"
                assert current_writes(transport) == charging_writes

                # Lowering the vehicle cap below its present SoC lets the car end
                # the charge itself: the target is reached, nothing is paused.
                hass.states.async_set(LIMIT, "60", {"unit_of_measurement": "%"})
                await hass.async_block_till_done()
                assert vehicle_target(hass, waiting) == "reached"
                assert hass.states.get(target).state == "60.0"
                assert hass.states.get(pause).state == "off"
                assert current_writes(transport) == charging_writes

                # A target below the car's limit requests a real pause.
                forget_current_write(hass, entry)
                await set_target(55)
                await settle(hass, entry)
                assert hass.states.get(pause).state == "on"
                assert vehicle_target(hass, waiting) == "reached"
                assert hass.states.get(target).state == "55.0"
                assert current_writes(transport)[-1] == 1

                # A cold goal owner must restore from disk without starting the car.
                before_reload = current_writes(transport)
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                assert vehicle_target(hass, waiting) == "reached"
                assert hass.states.get(target).state == "55.0"
                assert hass.states.get(pause).state == "on"
                assert current_writes(transport) == before_reload
                await set_target(0)
                assert vehicle_target(hass, waiting) == "disabled"
                assert hass.states.get(pause).state == "on"
                assert current_writes(transport) == before_reload
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
