"""The actual HA options flow guides missing detection before activation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.fakes import FakeModbusClient, device_bank
from tests.options_flow_helpers import assert_options_http, async_options
from tests.test_home_assistant_smoke import _boot
from tests.test_soc_goal_ha_lifecycle import _entry
from tests.test_vehicle_profiles import A, profile_options


@pytest.mark.parametrize("cancel", [False, True])
def test_automatic_selection_setup_waits_for_complete_signals_and_can_be_cancelled(tmp_path, cancel):
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
                form = await async_options(hass, entry, "vehicle", "vehicle_detection", "vehicle_detection_mode")
                manager = hass.config_entries.options
                form = await manager.async_configure(form["flow_id"], {"vehicle_auto_selection": True})
                assert_options_http(form)
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
                assert entry.options["vehicle_auto_selection"] is True
                assert entry.options["vehicle_profiles"][A]["detection"] == {
                    "source": "entities", "connected_entity": "binary_sensor.car_plugged_in",
                    "home_entity": "device_tracker.car",
                }
                # Missing live evidence is not permission to pick this car.
                assert entry.options["active_vehicle_profile"] is None
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=75))
