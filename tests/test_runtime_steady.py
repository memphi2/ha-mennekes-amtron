"""Continuous HA operation: no reloads, real timer deadlines, bounded resources."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import tracemalloc
from collections import deque
from pathlib import Path
from unittest.mock import patch

from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER, DOMAIN
from scripts.check_runtime_soak import sample_errors
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.test_home_assistant_smoke import _boot
from tests.test_runtime_soak import _sample


def test_continuously_loaded_resources_stay_bounded(tmp_path):
    seconds = float(os.environ.get("AMTRON_STEADY_SECONDS", "0.3"))

    async def run():
        def factory(*_args, **_kwargs):
            transport = FakeModbusClient(device_bank())
            transport.reads = deque(maxlen=16)
            transport.writes = deque(maxlen=16)
            return transport

        with patch("custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient", side_effect=factory), patch.object(
            logging.getLogger("custom_components.mennekes_amtron_rtu.coordinator"), "disabled", True
        ):
            hass = await _boot(tmp_path / "config")
            samples = []
            recoveries = 0
            try:
                flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                await hass.config_entries.flow.async_configure(flow["flow_id"], {
                    "port": "/dev/fake", "device_id": 50, "baudrate": 57600,
                    "frame": "8n2", "search": "off",
                })
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                hass.config_entries.async_update_entry(entry, options={"control_mode": CONTROL_MODE_MASTER})
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                client, coordinator = runtime.client, runtime.coordinator
                transport = client._client
                # Warm caches before measuring an unchanged, continuously loaded entry.
                await coordinator.async_refresh()
                await asyncio.sleep(min(5, seconds))
                tracemalloc.start()
                started = time.monotonic()
                next_recovery = started
                interval = min(5, seconds / 4)
                while time.monotonic() - started < seconds or len(samples) < 3:
                    await coordinator.async_refresh()
                    assert coordinator.last_update_success
                    if time.monotonic() >= next_recovery:
                        transport.read_exceptions[0x0100] = raise_connection_error()
                        await coordinator.async_refresh()
                        assert not coordinator.last_update_success
                        transport.read_exceptions.clear()
                        await coordinator.async_refresh()
                        assert coordinator.last_update_success
                        recoveries += 1
                        next_recovery = time.monotonic() + 30
                    assert entry.runtime_data is runtime
                    assert client.connected
                    assert runtime.heartbeat.running
                    assert len(runtime.diagnostics.timeline.snapshot()) <= 40
                    samples.append(_sample("steady", [], client, coordinator))
                    await asyncio.sleep(interval)
                elapsed = time.monotonic() - started
                heartbeats = runtime.diagnostics.heartbeats_sent
                assert heartbeats >= max(1, seconds / 10)
                assert not sample_errors(samples, phases=("steady",))
                assert await hass.config_entries.async_unload(entry.entry_id)
                await hass.async_block_till_done()
                assert not client.connected
                assert not runtime.heartbeat.running
                assert not coordinator._listeners
                assert runtime.control._flush_task is None
            finally:
                await hass.async_stop()
                tracemalloc.stop()
        return {"seconds": seconds, "elapsed": elapsed, "recoveries": recoveries,
                "heartbeats": heartbeats, "samples": samples}

    report = asyncio.run(asyncio.wait_for(run(), seconds + 90))
    if destination := os.environ.get("AMTRON_STEADY_REPORT"):
        Path(destination).write_text(json.dumps(report, indent=2) + "\n")
