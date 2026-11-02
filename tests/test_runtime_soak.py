"""Real HA lifecycle measurements with synthetic Modbus data and real port cleanup."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import tracemalloc
import weakref
from collections import deque
from pathlib import Path
from unittest.mock import patch

from custom_components.mennekes_amtron_rtu.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER, DOMAIN
from scripts.check_runtime_soak import sample_errors
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.test_home_assistant_smoke import _boot
from tests.test_serial_failures import silent_serial


def _fds():
    return len(list(Path("/proc/self/fd").iterdir()))


def _sample(phase, references, current, coordinator):
    gc.collect()
    loop = asyncio.get_running_loop()
    tasks = [task for task in asyncio.all_tasks() if not task.done()]
    owned = [task for task in tasks if "mennekes_amtron_rtu" in task.get_coro().cr_code.co_filename]
    memory = tracemalloc.take_snapshot()
    integration_bytes = sum(s.size for s in memory.statistics("filename")
                            if "/custom_components/mennekes_amtron_rtu/" in s.traceback[0].filename)
    return {
        "phase": phase, "tasks": len(tasks), "owned_tasks": len(owned),
        "timers": sum(not timer.cancelled() for timer in loop._scheduled),
        "fds": _fds(), "listeners": len(coordinator._listeners),
        "integration_bytes": integration_bytes, "traced_bytes": tracemalloc.get_traced_memory()[0],
        "rss_bytes": int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE"),
        "retained_clients": sum(ref() is not None and ref() is not current for ref in references),
        "open_clients": int(current.connected),
    }


def test_lifecycle_resources_stay_bounded(tmp_path):
    async def run():
        cycles = int(os.environ.get("AMTRON_SOAK_CYCLES", "3"))
        idle = float(os.environ.get("AMTRON_SOAK_IDLE", "0.1"))
        references = []
        samples = []

        def transport_factory(*_args, **_kwargs):
            transport = FakeModbusClient(device_bank())
            transport.reads = deque(maxlen=16)
            transport.writes = deque(maxlen=16)
            return transport

        # Pytest retains exception LogRecords (and their client frames) until
        # the test ends. Expected injected poll failures must not become fake leaks.
        with patch("custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient",
                   side_effect=transport_factory), patch.object(
                       logging.getLogger("custom_components.mennekes_amtron_rtu.coordinator"),
                       "disabled", True,
                   ):
            hass = await _boot(tmp_path / "config")
            tracemalloc.start()
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

                async def cycle(index):
                    runtime = entry.runtime_data
                    client, coordinator = runtime.client, runtime.coordinator
                    transport = client._client
                    references.append(weakref.ref(client))
                    if index >= 2:
                        samples.append(_sample("loaded", references, client, coordinator))
                    transport.read_exceptions[0x0100] = raise_connection_error()
                    await coordinator.async_refresh()
                    assert not coordinator.last_update_success
                    transport.read_exceptions.clear()
                    await coordinator.async_refresh()
                    assert coordinator.last_update_success
                    if index >= 2:
                        samples.append(_sample("recovered", references, client, coordinator))
                    await asyncio.sleep(idle / cycles if index >= 2 else 0)
                    if index >= 2:
                        samples.append(_sample("idle", references, client, coordinator))
                    await runtime.control.async_set_charging_current(8)
                    await runtime.control.async_set_charging_current(9)
                    assert runtime.control._flush_task is not None
                    assert await hass.config_entries.async_unload(entry.entry_id)
                    await hass.async_block_till_done()
                    assert not client.connected
                    assert not runtime.heartbeat.running
                    assert runtime.control._flush_task is None
                    if index >= 2:
                        samples.append(_sample("unloaded", references, client, coordinator))

                for index in range(cycles + 2):
                    if index:
                        assert await hass.config_entries.async_setup(entry.entry_id)
                        await hass.async_block_till_done()
                    await cycle(index)
            finally:
                await hass.async_stop()
                tracemalloc.stop()
        gc.collect()
        assert all(ref() is None for ref in references)
        assert not sample_errors(samples), sample_errors(samples)
        before = _fds()
        for _ in range(cycles):
            with silent_serial() as (port, _frames, _event):
                for _attempt in range(2):
                    client = MennekesModbusClient(SerialConfig(port=port))
                    try:
                        await client.async_connect()
                        assert client.connected
                    finally:
                        await client.async_close()
                    await asyncio.sleep(0)
        growth = _fds() - before
        assert growth == 0
        return {"cycles": cycles, "idle_seconds": idle, "serial_cycles": cycles,
                  "serial_fd_growth": growth, "samples": samples}

    report = asyncio.run(asyncio.wait_for(run(), 300))
    if destination := os.environ.get("AMTRON_SOAK_REPORT"):
        Path(destination).write_text(json.dumps(report, indent=2) + "\n")
