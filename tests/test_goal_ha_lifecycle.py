"""Exercise the energy ceiling through real HA services, entities and storage."""

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
from tests.fakes import FakeModbusClient, device_bank, put
from tests.options_flow_helpers import async_options
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes


def test_energy_goal_services_stop_and_restore_without_resuming(tmp_path: Path) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank(charging_current=8))
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"},
                )
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
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                await async_options(
                    hass, entry, "wallbox", "wallbox_basic",
                    {"control_mode": CONTROL_MODE_MASTER, "scan_interval_seconds": 300, "current_limit": 16},
                    "edit", "wallbox_advanced",
                    {"measurement_interval_seconds": 300, "state_change_interval_seconds": 0, "phase_switch_grace_seconds": 0},
                    "save",
                )
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                registry = er.async_get(hass)
                target = registry.async_get_entity_id(
                    "number", DOMAIN, f"{SERIAL_NUMBER}_energy_goal",
                )
                status = registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{SERIAL_NUMBER}_energy_goal_status",
                )
                pause = registry.async_get_entity_id(
                    "switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused",
                )
                assert target is not None
                assert status is not None
                assert pause is not None
                assert hass.states.get(status).state == "disabled"

                # A real pause service establishes explicit intent. Setting a
                # target must publish its entities without removing that pause.
                runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call(
                    "switch", "turn_on", {"entity_id": pause}, blocking=True,
                )
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(pause).state == "on"
                before_goal = current_writes(transport)
                await hass.services.async_call(
                    "number", "set_value", {"entity_id": target, "value": 1},
                    blocking=True,
                )
                await hass.async_block_till_done()
                assert hass.states.get(target).state == "1.0"
                assert hass.states.get(status).state == "active"
                assert hass.states.get(pause).state == "on"
                assert runtime.control.pause_requested is True
                assert current_writes(transport) == before_goal
                baseline = runtime.control.energy_goal.baseline_kwh
                assert baseline is not None

                # Only the user's separate resume action may start charging.
                runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                await hass.services.async_call(
                    "switch", "turn_off", {"entity_id": pause}, blocking=True,
                )
                await runtime.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(pause).state == "off"
                assert current_writes(transport)[-1] == 8

                # Keep the real coordinator debounce: the goal worker's pause
                # must reach HA without a second forced refresh from the test.
                stopped = asyncio.Event()

                @callback
                def state_changed(event):
                    state = event.data["new_state"]
                    if state is not None and state.state == "on":
                        stopped.set()

                unsubscribe = async_track_state_change_event(hass, pause, state_changed)
                try:
                    runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
                    put(transport.bank, R.ENERGY_TOTAL, baseline + 1)
                    runtime.coordinator._poller._slow_read_at = None
                    await runtime.coordinator.async_refresh()
                    await asyncio.wait_for(stopped.wait(), 15)
                    await hass.async_block_till_done()
                finally:
                    unsubscribe()
                assert hass.states.get(status).state == "reached"
                assert hass.states.get(target).state == "1.0"
                assert current_writes(transport)[-1] == 1
                assert runtime.control.pause_requested is True

                # Force restoration from disk rather than the reload cache.
                before_reload = current_writes(transport)
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.data[f"{DOMAIN}_write_states"].pop(entry.entry_id)
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                if task := runtime.goal._task:
                    await asyncio.wait_for(task, 5)
                await hass.async_block_till_done()
                assert hass.states.get(status).state == "reached"
                assert hass.states.get(target).state == "1.0"
                assert hass.states.get(pause).state == "on"
                assert runtime.control.energy_goal.baseline_kwh == baseline
                assert current_writes(transport) == before_reload

                await hass.services.async_call(
                    "number", "set_value", {"entity_id": target, "value": 0},
                    blocking=True,
                )
                await hass.async_block_till_done()
                assert hass.states.get(target).state == "0.0"
                assert hass.states.get(status).state == "disabled"
                assert hass.states.get(pause).state == "on"
                assert runtime.control.pause_requested is True
                assert current_writes(transport) == before_reload
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
