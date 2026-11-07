"""An older saved tariff must never prevent wallbox startup and heartbeats."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_soc_goal_ha_lifecycle import _entry


def test_saved_minute_precision_module_three_tariff_survives_ha_restart(tmp_path):
    async def run():
        config = tmp_path / "ha"
        transport = FakeModbusClient(device_bank())
        # Version 0.6.0b1 accepted minute-level windows. Preserve this saved
        # configuration even though new inputs require quarter-hour boundaries.
        legacy_tariff = {
            "module": "module_3", "regular_network": 0.1,
            "price_includes_network": True, "high_network": 0.2, "low_network": 0.01,
            "windows": [
                {"start": "06:01", "end": "09:01", "rate": "ht"},
                {"start": "22:00", "end": "06:00", "rate": "nt"},
            ],
            "active_quarters": [1, 4],
            "tariff_year": datetime.now(ZoneInfo("Europe/Berlin")).year,
            "timezone": "Europe/Berlin",
        }
        with patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport):
            hass = await _boot(config)
            try:
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(entry, options={
                    CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
                    "charging_cost_source": "fixed", "charging_price": 0.3,
                    "charging_network_tariff": legacy_tariff,
                })
            finally:
                await hass.async_stop()

            # Keep HA's actual config-entry/storage files and start fresh
            # runtime owners, as happens when installing an integration update.
            shutil.rmtree(config / "custom_components" / DOMAIN)
            hass = await _boot(config)
            try:
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                assert entry.options["charging_network_tariff"] == legacy_tariff
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                runtime = entry.runtime_data
                assert runtime.heartbeat.running
                assert runtime.coordinator.last_update_success
                assert runtime.client.connected
                assert runtime.charging_cost._tariff.windows[0].start == 361
                registry = er.async_get(hass)
                current = registry.async_get_entity_id("number", DOMAIN, f"{SERIAL_NUMBER}_charging_current_limit")
                cost = registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL_NUMBER}_last_estimated_charging_cost")
                assert current is not None
                assert hass.states.get(current).state != "unavailable"
                assert cost is not None
                assert hass.states.get(cost) is not None
                assert entry.options["charging_network_tariff"] == legacy_tariff
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
