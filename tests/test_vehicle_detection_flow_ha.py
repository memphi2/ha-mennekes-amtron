"""The actual HA options flow configures a profile's detection without activating it."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu.vehicle_detection import (
    automatic_selection_available,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.options_flow_helpers import assert_options_http, async_options
from tests.test_home_assistant_smoke import _boot
from tests.test_soc_goal_ha_lifecycle import _entry
from tests.test_vehicle_profiles import A, profile_options


@pytest.mark.parametrize("cancel", [False, True])
def test_profile_detection_setup_saves_complete_signals_and_can_be_cancelled(tmp_path, cancel):
    async def run():
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=FakeModbusClient(device_bank()),
        ):
            hass = await _boot(tmp_path / "config")
            try:
                entry = await _entry(hass)
                options = profile_options(provider="none")
                options.update({"scan_interval_seconds": 300, "measurement_interval_seconds": 300})
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                original = deepcopy(dict(entry.options))
                form = await async_options(
                    hass, entry, "vehicle", "vehicle_profile_open", {"vehicle_profile_id": A},
                    "vehicle_detection_signals",
                )
                manager = hass.config_entries.options
                assert form["step_id"] == "vehicle_detection_source"
                assert form["errors"] == {}
                form = await manager.async_configure(form["flow_id"], {"vehicle_detection_source": "entities"})
                assert_options_http(form)
                assert form["step_id"] == "vehicle_detection_entities"
                assert entry.options == original
                if cancel:
                    manager.async_abort(form["flow_id"])
                    assert entry.options == original
                    return
                result = await manager.async_configure(form["flow_id"], {
                    "vehicle_connected_entity": "binary_sensor.car_plugged_in",
                    "vehicle_home_entity": "device_tracker.car",
                })
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                # Automatic selection is chosen in the Active vehicle entity.
                assert not entry.options.get("vehicle_auto_selection")
                assert automatic_selection_available(entry.options)
                assert entry.options["vehicle_profiles"][A]["detection"] == {
                    "source": "entities", "connected_entity": "binary_sensor.car_plugged_in",
                    "home_entity": "device_tracker.car",
                }
                # Missing live evidence neither picks nor drops the chosen car.
                assert entry.options["active_vehicle_profile"] == original["active_vehicle_profile"]
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
