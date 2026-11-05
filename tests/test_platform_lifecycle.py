"""Mode changes unload the previous runtime's entities inside real HA."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot
from tests.test_service_mode_coupling import CONTROL_ENTITY_IDS


def test_direct_mode_changes_unload_previous_controls_without_platform_warnings(
    tmp_path, caplog
):
    async def run():
        hass = await _boot(tmp_path / "ha")
        transport = FakeModbusClient(device_bank())
        try:
            # Other integrations usually load these components first. Without
            # them, HA skips forwarding an unload and hides the incorrect set.
            for platform in ("number", "switch", "select", "button"):
                assert await async_setup_component(hass, platform, {})
            with patch(
                "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
                return_value=transport,
            ):
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"next_step_id": "serial"}
                )
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "port": "/dev/fake",
                        "device_id": 50,
                        "baudrate": 57600,
                        "frame": "8n2",
                        "search": "off",
                    },
                )
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                assert entry.state is ConfigEntryState.LOADED
                assert all(hass.states.get(key) is None for key in CONTROL_ENTITY_IDS)
                registry = er.async_get(hass)
                registered = None

                for mode in ("master", "read_only", "master"):
                    options = await hass.config_entries.options.async_init(entry.entry_id)
                    await hass.config_entries.options.async_configure(
                        options["flow_id"],
                        {
                            "control_mode": mode,
                            "current_limit": 16,
                            "scan_interval_seconds": 300,
                            "measurement_interval_seconds": 300,
                        },
                    )
                    await hass.async_block_till_done()
                    assert entry.state is ConfigEntryState.LOADED
                    for entity_id in CONTROL_ENTITY_IDS:
                        state = hass.states.get(entity_id)
                        assert state is not None
                        assert (state.state == "unavailable") == (mode == "read_only")
                    current = {
                        entity.unique_id: (entity.entity_id, entity.device_id)
                        for entity in er.async_entries_for_config_entry(
                            registry, entry.entry_id
                        )
                    }
                    if registered is None:
                        registered = current
                    else:
                        assert registered == current
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert all(
                    hass.states.get(entity_id).state == "unavailable"
                    for entity_id in CONTROL_ENTITY_IDS
                )
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
    assert "Ignored unload request" not in caplog.text
    assert "Error unloading entry" not in caplog.text
    assert "already been setup" not in caplog.text
