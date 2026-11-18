"""Exercise event registration, restoration and removal inside real HA."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import EVENT_LOGBOOK_ENTRY, EVENT_STATE_CHANGED, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot


def test_activity_command_poll_and_reload_lifecycle(tmp_path: Path) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        with (
            patch(
                "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
                return_value=transport,
            ),
            patch.object(config_entries, "RELOAD_AFTER_UPDATE_DELAY", 0),
        ):
            hass = await _boot(tmp_path / "config")
            try:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"next_step_id": "serial"}
                )
                await hass.config_entries.flow.async_configure(flow["flow_id"], {
                    "port": "/dev/fake", "device_id": 50, "baudrate": 57600,
                    "frame": "8n2", "search": "off",
                })
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                hass.config_entries.async_update_entry(
                    entry, options={"control_mode": CONTROL_MODE_MASTER}
                )
                await hass.async_block_till_done()
                registry = er.async_get(hass)
                registered = er.async_entries_for_config_entry(registry, entry.entry_id)
                event_id = next(
                    item.entity_id for item in registered
                    if item.unique_id.endswith("_activity")
                )
                number_id = next(
                    item.entity_id for item in registered
                    if item.unique_id.endswith("_charging_current_limit")
                )
                # Activity is enabled by default and starts without a baseline event.
                assert registry.async_get(event_id).disabled_by is None
                assert hass.states.get(event_id).state == STATE_UNKNOWN
                assert hass.states.get(event_id).attributes["event_type"] is None
                service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service

                activity = []
                remove_activity = hass.bus.async_listen(EVENT_LOGBOOK_ENTRY, activity.append)
                await hass.services.async_call(
                    "number", "set_value", {"entity_id": number_id, "value": 10},
                    blocking=True,
                )
                if pending := service.control._flush_task:
                    await pending
                await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                recorded = hass.states.get(event_id)
                assert dt_util.parse_datetime(recorded.state) is not None
                assert {
                    key: recorded.attributes[key]
                    for key in ("event_type", "action", "result", "reason", "trigger", "value")
                } == {
                    "event_type": "current_set", "action": "set_current", "result": "executed",
                    "reason": "completed", "trigger": "home_assistant", "value": 10,
                }

                # The entity's own row is the only logbook entry.
                assert activity == []
                changes = []
                remove = hass.bus.async_listen(
                    EVENT_STATE_CHANGED,
                    lambda event: changes.append(event)
                    if event.data["entity_id"] == event_id else None,
                )
                for _ in range(3):
                    await service.refresh()
                    await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert changes == []
                assert activity == []
                remove_activity()
                remove()

                old_feed = entry.runtime_data.control.events
                assert len(old_feed._listeners) == 1
                assert await hass.config_entries.async_unload(entry.entry_id)
                await hass.async_block_till_done()
                assert not old_feed._listeners
                old_feed.record("failed", "resume", "connection_error")
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()

                # A reload shows neither the last command again nor a buffered one.
                restored = hass.states.get(event_id)
                assert restored.state == "unknown"
                assert restored.attributes["event_type"] is None
                assert entry.runtime_data.control.events is not old_feed
                assert entry.runtime_data.control.events.snapshot()["events"] == []
                assert len(entry.runtime_data.control.events._listeners) == 1
                await entry.runtime_data.control.async_set_lock_evse(False)
                await hass.async_block_till_done()
                assert hass.states.get(event_id).state > recorded.state
                assert hass.states.get(event_id).attributes["action"] == "unlock"
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
