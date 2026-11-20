"""Unchanged polls stay quiet without hiding failures or repair conditions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank, put, raise_connection_error
from tests.ha_fakes import build_runtime
from tests.test_home_assistant_smoke import _boot


def test_identical_polls_are_quiet_but_changes_failures_and_repairs_are_visible(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial"})
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
                service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service
                async def poll() -> None:
                    # The service reads the wallbox on its own clock; read it
                    # now so the HA refresh sees this step's wallbox state.
                    await service.refresh()
                    await coordinator.async_refresh()

                coordinator.update_interval = None
                coordinator.async_set_updated_data(coordinator.data)
                notifications = []
                remove = coordinator.async_add_listener(
                    lambda: notifications.append(coordinator.last_update_success)
                )
                for _ in range(10):
                    await poll()
                assert notifications == []

                issues = ir.async_get(hass).issues
                heartbeat_issue = (DOMAIN, f"{entry.entry_id}_ems_heartbeat_lost")
                put(transport.bank, R.ERROR_CODE, 200)
                await poll()
                assert notifications == [True]
                assert heartbeat_issue in issues
                put(transport.bank, R.ERROR_CODE, 0)
                await poll()
                assert heartbeat_issue not in issues
                assert notifications == [True, True]

                seen = len(service.diagnostics.timeline.snapshot())
                transport.read_exceptions[0x0100] = raise_connection_error()
                with pytest.raises(AmtronConnectionError):
                    await service.refresh()
                await coordinator.async_refresh()
                assert notifications[-1] is False
                transport.read_exceptions.clear()
                await poll()
                assert notifications == [True, True, False, True]
                assert [item["event"] for item in service.diagnostics.timeline.snapshot()[seen:]] == [
                    "connection_lost", "connection_recovered",
                ]

                transport.write_exceptions[R.CHARGING_RELEASE.address] = 0x03
                with pytest.raises(HomeAssistantError):
                    await entry.runtime_data.control.async_set_charging_release(True)
                # The rejection reaches HA with the next state read, which
                # must still not notify entities: no register changed.
                await coordinator.async_refresh()
                assert (DOMAIN, f"{entry.entry_id}_write_rejected") in issues
                assert notifications == [True, True, False, True]
                assert service.diagnostics.timeline.snapshot()[-1]["event"] == "write_rejected"
                # A different successful command must not hide the failed one.
                await entry.runtime_data.control.async_set_lock_evse(False)
                assert (DOMAIN, f"{entry.entry_id}_write_rejected") in issues
                transport.write_exceptions.clear()
                await entry.runtime_data.control.async_set_charging_release(True)
                assert (DOMAIN, f"{entry.entry_id}_write_rejected") not in issues
                assert service.diagnostics.writes_rejected == 1
                remove()
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))


def test_a_decision_of_the_service_notifies_although_every_register_is_unchanged() -> None:
    async def run() -> None:
        _hass, entry, wallbox = await build_runtime()
        coordinator = entry.runtime_data.coordinator
        calls: list[None] = []
        unsubscribe = coordinator.async_add_listener(lambda: calls.append(None))
        try:
            await coordinator.async_refresh()
            calls.clear()
            # Unchanged polls stay quiet.
            await coordinator.async_refresh()
            await coordinator.async_refresh()
            assert calls == []
            data = coordinator.data
            control = entry.runtime_data.client.snapshot["control"]
            assert control["departure"]["trip"]["deadline"] is None
            deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=8)
            await entry.service.command({
                "command": "departure_choice", "deadline": deadline.isoformat(),
                "revision": control["target_time_revision"],
                "expected_serial_number": "ABC123456789",
            })
            # Only the service's own decision changed, no register.
            assert wallbox.writes == []
            await coordinator.async_refresh()
            assert len(calls) == 1
            assert coordinator.data.values == data.values
            trip = entry.runtime_data.client.snapshot["control"]["departure"]["trip"]
            assert datetime.fromisoformat(trip["deadline"]) == deadline
            await coordinator.async_refresh()
            assert len(calls) == 1
        finally:
            unsubscribe()
            await entry.service.stop()

    asyncio.run(run())
