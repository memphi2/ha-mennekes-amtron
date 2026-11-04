"""Exercise reload ownership in the real config-entry manager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("loaded", [False, True])
def test_reconfigure_reloads_once_only_when_changed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, changed: bool, loaded: bool
) -> None:
    async def run() -> None:
        clients: list[FakeModbusClient] = []

        class ExclusiveClient(FakeModbusClient):
            async def connect(self) -> bool:
                if any(client.connected and client is not self for client in clients):
                    raise OSError("serial port is already exclusively locked")
                return await super().connect()

        def factory(*args, **kwargs):
            client = ExclusiveClient(device_bank())
            clients.append(client)
            return client

        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            side_effect=factory,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                data = {
                    "port": "/dev/fake",
                    "device_id": 50,
                    "baudrate": 57600,
                    "frame": "8n2",
                    "search": "off",
                }
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial"})
                await hass.config_entries.flow.async_configure(flow["flow_id"], data)
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                previous_runtime = entry.runtime_data
                if not loaded:
                    assert await hass.config_entries.async_unload(entry.entry_id)
                    assert not entry.update_listeners
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "reconfigure", "entry_id": entry.entry_id},
                )
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial_reconfigure"})
                if changed:
                    data["baudrate"] = 38400
                with patch.object(
                    hass.config_entries,
                    "async_reload",
                    wraps=hass.config_entries.async_reload,
                ) as reload_entry:
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], data
                    )
                    await hass.async_block_till_done()
                    assert result["reason"] == "reconfigure_successful"
                    assert reload_entry.await_count == int(changed or not loaded)
                assert (entry.runtime_data is not previous_runtime) == (
                    changed or not loaded
                )
                assert entry.data["baudrate"] == data["baudrate"]
                assert len(entry.update_listeners) == 1
                assert not any(
                    "has an update listener" in record.message
                    for record in caplog.records
                )
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))


def test_removing_entry_clears_only_its_repair_issues(tmp_path: Path) -> None:
    async def run() -> None:
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=FakeModbusClient(
                device_bank(error_code=200, fallback_current=0)
            ),
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
                issues = ir.async_get(hass).issues
                owned = {(domain, key) for domain, key in issues if domain == DOMAIN}
                assert len(owned) == 2
                unrelated = {
                    (DOMAIN, "other_entry_issue"),
                    ("other_domain", f"{entry.entry_id}_issue"),
                }
                for domain, key in unrelated:
                    ir.async_create_issue(
                        hass,
                        domain,
                        key,
                        is_fixable=False,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key="example",
                    )
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert owned <= issues.keys()  # Unload/reload must retain repairs.
                await hass.config_entries.async_remove(entry.entry_id)
                await hass.async_block_till_done()
                assert owned.isdisjoint(issues)
                assert unrelated <= issues.keys()
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
