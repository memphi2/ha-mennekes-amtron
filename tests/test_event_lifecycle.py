"""Exercise event registration, restoration and removal inside real HA."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot


def test_event_opt_in_command_poll_and_reload_lifecycle(tmp_path: Path) -> None:
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
                event_id = next(item.entity_id for item in registered if item.domain == "event")
                number_id = next(
                    item.entity_id for item in registered
                    if item.unique_id.endswith("_charging_current_limit")
                )
                assert registry.async_get(event_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
                assert hass.states.get(event_id) is None

                # Explicit registry opt-in uses HA's actual entry reload path.
                entry.runtime_data.control.events.record("executed", "lock")
                enabled = asyncio.Event()
                remove_enabled = hass.bus.async_listen(
                    EVENT_STATE_CHANGED,
                    lambda event: enabled.set()
                    if event.data["entity_id"] == event_id
                    and event.data["new_state"] is not None
                    and event.data["new_state"].state == STATE_UNKNOWN else None,
                )
                registry.async_update_entity(event_id, disabled_by=None)
                await asyncio.wait_for(enabled.wait(), 10)
                await hass.async_block_till_done()
                remove_enabled()
                assert hass.states.get(event_id).state == STATE_UNKNOWN
                assert hass.states.get(event_id).attributes["event_type"] is None

                await hass.services.async_call(
                    "number", "set_value", {"entity_id": number_id, "value": 10},
                    blocking=True,
                )
                if pending := entry.runtime_data.control._flush_task:
                    await pending
                await hass.async_block_till_done()
                recorded = hass.states.get(event_id)
                assert dt_util.parse_datetime(recorded.state) is not None
                assert {key: recorded.attributes[key] for key in ("event_type", "action", "reason")} == {
                    "event_type": "set_current_executed", "action": "set_current", "reason": "completed",
                }

                changes = []
                remove = hass.bus.async_listen(
                    EVENT_STATE_CHANGED,
                    lambda event: changes.append(event)
                    if event.data["entity_id"] == event_id else None,
                )
                for _ in range(3):
                    await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert changes == []
                remove()

                old_feed = entry.runtime_data.control.events
                assert len(old_feed._listeners) == 1
                assert await hass.config_entries.async_unload(entry.entry_id)
                await hass.async_block_till_done()
                assert not old_feed._listeners
                old_feed.record("failed", "resume", "connection_error")
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()

                # Restore the old timestamp, never replay a buffered command.
                restored = hass.states.get(event_id)
                assert restored.state == recorded.state
                assert restored.attributes == recorded.attributes
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
