"""Continuous HA operation over real serial transports with a simulated wallbox."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import tracemalloc
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
    HEARTBEAT_DEADLINE_SECONDS,
)
from custom_components.mennekes_amtron_rtu.decode import encode_register
from custom_components.mennekes_amtron_rtu.enums import EvseState
from scripts.check_runtime_soak import sample_errors
from tests.fakes import device_bank
from tests.heartbeat_measurement import HeartbeatMeasurement
from tests.test_home_assistant_smoke import _boot, _start_wallbox
from tests.test_runtime_soak import _sample
from tests.test_serial_loopback import _virtual_serial_link


def test_continuously_loaded_resources_stay_bounded(tmp_path, request):
    seconds = float(os.environ.get("AMTRON_STEADY_SECONDS", "10"))
    device_port, client_port = _virtual_serial_link(request)
    report = {"status": "running", "stage": "boot", "seconds": seconds,
              "elapsed": 0, "recoveries": 0, "heartbeats": 0,
              "measurement_changes": 0, "transport": "serial_loopback", "samples": []}

    def checkpoint():
        if destination := os.environ.get("AMTRON_STEADY_REPORT"):
            path = Path(destination)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, indent=2) + "\n")
            temporary.replace(path)

    checkpoint()

    async def run():
        load_enabled = False

        async def slow_reads(function, _start, _address, _count, _registers, values):
            if load_enabled and function == 3 and values is None:
                await asyncio.sleep(0.02)

        server, serving = await _start_wallbox(device_port, device_bank(), action=slow_reads)
        hass = None
        samples = report["samples"]
        recoveries = 0
        changes = 0
        last_power = None
        started = None

        def record_progress():
            if started is not None:
                now = time.monotonic()
                report.update(elapsed=now - started, recoveries=recoveries,
                              heartbeats=measured.deliveries,
                              heartbeat_failures=runtime.diagnostics.heartbeats_failed,
                              heartbeats_late=runtime.diagnostics.heartbeats_late,
                              heartbeat_max_gap=measured.maximum_gap(now),
                              measurement_changes=changes)
            checkpoint()

        try:
            hass = await _boot(tmp_path / "config")
            flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
            await hass.config_entries.flow.async_configure(flow["flow_id"], {
                "port": client_port, "device_id": 50, "baudrate": 57600,
                "frame": "8n2", "search": "off",
            })
            await hass.async_block_till_done()
            entry = hass.config_entries.async_entries(DOMAIN)[0]
            hass.config_entries.async_update_entry(entry, options={"control_mode": CONTROL_MODE_MASTER})
            await hass.async_block_till_done()
            runtime = entry.runtime_data
            client, coordinator = runtime.client, runtime.coordinator
            measured = HeartbeatMeasurement(time.monotonic())
            original_write = client.async_write_register

            async def observed_write(spec, value, **kwargs):
                await original_write(spec, value, **kwargs)
                if spec == R.HEARTBEAT:
                    measured.delivered(time.monotonic())

            client.async_write_register = observed_write
            await coordinator.async_refresh()
            await asyncio.sleep(min(5, seconds))
            load_enabled = True
            tracemalloc.start()
            started = time.monotonic()
            report["stage"] = "running"
            next_recovery = started
            interval = min(5, seconds / 4)
            while time.monotonic() - started < seconds or len(samples) < 3:
                if time.monotonic() >= next_recovery:
                    # Exercise the driver's connection-lost path: it really
                    # closes the FD and pymodbus reconnects in the background.
                    transport = client._client
                    async with client._lock:
                        link = transport.ctx.transport
                        link.close(ConnectionResetError("simulated serial disconnect"))
                        assert link.sync_serial is None
                        assert not client.connected
                        reconnect = transport.ctx.reconnect_task
                        assert reconnect is not None
                    # Avoid pytest retaining expected exception frames as leaks.
                    with patch.object(logging.getLogger(coordinator.__module__), "disabled", True):
                        await coordinator.async_refresh()
                    assert not coordinator.last_update_success
                    async with asyncio.timeout(3):
                        await reconnect
                    assert client.connected
                    await coordinator.async_refresh()
                    assert coordinator.last_update_success
                    assert client._client is transport
                    assert transport.ctx.transport is not link
                    recoveries += 1
                    next_recovery = time.monotonic() + 30
                index = len(samples)
                for spec, value in (
                    (R.EVSE_STATE, int(EvseState.CHARGING if index % 2 else EvseState.EV_CONNECTED)),
                    (R.POWER_TOTAL, 1000 + (index % 100) * 30),
                    (R.VOLTAGE_L1, 229 + (index % 10) / 10),
                    (R.ENERGY_TOTAL, 1234.5 + index / 100),
                ):
                    await server.async_setValues(50, 0x10, spec.address, encode_register(spec, value))
                # Exercise the real bus locks with concurrent reads and delayed
                # responses while the periodic heartbeat must meet its deadline.
                await asyncio.gather(coordinator.async_refresh(), *(
                    client.async_read_register(R.VOLTAGE_L1) for _ in range(6)
                ))
                assert coordinator.last_update_success
                power = coordinator.data.get("power_total")
                assert power is not None
                if last_power is not None and power != last_power:
                    changes += 1
                last_power = power
                assert entry.runtime_data is runtime
                assert runtime.heartbeat.running
                assert len(runtime.diagnostics.timeline.snapshot()) <= 40
                assert measured.maximum_gap(time.monotonic()) <= HEARTBEAT_DEADLINE_SECONDS
                samples.append(_sample("steady", [], client, coordinator))
                record_progress()
                await asyncio.sleep(interval)
            assert measured.maximum_gap(time.monotonic()) <= HEARTBEAT_DEADLINE_SECONDS
            assert measured.deliveries >= seconds / 10
            assert changes >= 2
            assert not sample_errors(samples, phases=("steady",))
            record_progress()
            report["stage"] = "cleanup"
            checkpoint()
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
            assert not client.connected
            assert not runtime.heartbeat.running
            assert not coordinator._listeners
            assert runtime.control._flush_task is None
        finally:
            if report["stage"] != "cleanup":
                record_progress()
            if hass is not None:
                await hass.async_stop()
            await server.shutdown()
            serving.cancel()
            with suppress(asyncio.CancelledError):
                await serving
            tracemalloc.stop()

    try:
        asyncio.run(asyncio.wait_for(run(), seconds + 90))
    except BaseException as err:
        report.update(status="failed", failure_type=type(err).__name__)
        checkpoint()
        raise
    else:
        report.update(status="passed", stage="complete")
        checkpoint()
