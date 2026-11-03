"""Unchanged polls stay quiet without hiding failures or repair conditions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER, DOMAIN
from tests.fakes import FakeModbusClient, device_bank, put, raise_connection_error
from tests.test_home_assistant_smoke import _boot


def test_identical_polls_are_quiet_but_changes_failures_and_repairs_are_visible(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        with patch(
            "custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "port": "/dev/fake",
                        "device_id": 50,
                        "baudrate": 57600,
                        "frame": "8n2",
                        "search": "off",
                    },
                )
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                hass.config_entries.async_update_entry(
                    entry, options={"control_mode": CONTROL_MODE_MASTER}
                )
                await hass.async_block_till_done()
                coordinator = entry.runtime_data.coordinator
                coordinator.update_interval = None
                coordinator.async_set_updated_data(coordinator.data)
                notifications = []
                remove = coordinator.async_add_listener(
                    lambda: notifications.append(coordinator.last_update_success)
                )
                for _ in range(10):
                    await coordinator.async_refresh()
                assert notifications == []

                issues = ir.async_get(hass).issues
                heartbeat_issue = (DOMAIN, f"{entry.entry_id}_ems_heartbeat_lost")
                put(transport.bank, R.ERROR_CODE, 200)
                await coordinator.async_refresh()
                assert notifications == [True]
                assert heartbeat_issue in issues
                put(transport.bank, R.ERROR_CODE, 0)
                await coordinator.async_refresh()
                assert heartbeat_issue not in issues
                assert notifications == [True, True]

                transport.read_exceptions[0x0100] = raise_connection_error()
                await coordinator.async_refresh()
                assert notifications[-1] is False
                transport.read_exceptions.clear()
                await coordinator.async_refresh()
                assert notifications == [True, True, False, True]
                assert [item["event"] for item in entry.runtime_data.diagnostics.timeline.snapshot()] == [
                    "connection_lost", "connection_recovered",
                ]

                transport.write_exceptions[R.CHARGING_RELEASE.address] = 0x03
                with pytest.raises(HomeAssistantError):
                    await entry.runtime_data.control.async_set_charging_release(True)
                assert (DOMAIN, f"{entry.entry_id}_write_rejected") in issues
                assert notifications == [True, True, False, True]
                assert entry.runtime_data.diagnostics.timeline.snapshot()[-1]["event"] == "write_rejected"
                # A different successful command must not hide the failed one.
                await entry.runtime_data.control.async_set_lock_evse(False)
                assert (DOMAIN, f"{entry.entry_id}_write_rejected") in issues
                transport.write_exceptions.clear()
                await entry.runtime_data.control.async_set_charging_release(True)
                assert (DOMAIN, f"{entry.entry_id}_write_rejected") not in issues
                assert entry.runtime_data.diagnostics.writes_rejected == 1
                remove()
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
