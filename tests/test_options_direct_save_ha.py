"""Exercise immediate section saves and conflict checks through real HA."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType

from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot
from tests.test_soc_goal_ha_lifecycle import _entry


def test_options_dialog_commits_on_last_form_and_rejects_concurrent_change(tmp_path):
    async def run():
        transport = FakeModbusClient(device_bank())
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                manager = hass.config_entries.options

                async def begin():
                    form = await manager.async_init(entry.entry_id)
                    form = await manager.async_configure(form["flow_id"], {"next_step_id": "wallbox"})
                    return await manager.async_configure(form["flow_id"], {"next_step_id": "wallbox_basic"})

                form = await begin()
                assert form["last_step"] is True
                result = await manager.async_configure(form["flow_id"], {
                    "scan_interval_seconds": 10, "current_limit": 12,
                    "control_mode": "read_only",
                })
                assert result["type"] is FlowResultType.CREATE_ENTRY
                assert entry.options["current_limit"] == 12

                form = await begin()
                hass.config_entries.async_update_entry(entry, options={**entry.options, "current_limit": 14})
                result = await manager.async_configure(form["flow_id"], {
                    "scan_interval_seconds": 10, "current_limit": 8,
                    "control_mode": "read_only",
                })
                assert result["type"] is FlowResultType.FORM
                assert result["step_id"] == "options_changed"
                assert entry.options["current_limit"] == 14
                manager.async_abort(form["flow_id"])

                form = await begin()
                manager.async_abort(form["flow_id"])
                assert entry.options["current_limit"] == 14
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
