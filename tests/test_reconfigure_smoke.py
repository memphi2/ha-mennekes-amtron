"""Exercise reload ownership in the real config-entry manager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("loaded", [False, True])
def test_reconfigure_reloads_once_only_when_changed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, changed: bool, loaded: bool
) -> None:
    async def run() -> None:
        with patch(
            "custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient",
            side_effect=lambda *args, **kwargs: FakeModbusClient(device_bank()),
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
