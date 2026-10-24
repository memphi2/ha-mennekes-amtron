"""Replay synthetic register values through real Home Assistant and Recorder.

Run: python -m tests.recorder_replay /tmp/amtron-replay --measurement-interval 30
Only the Modbus transport is fake. No hardware or live HA instance is touched.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from homeassistant.components.recorder import get_instance
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank, put
from tests.test_home_assistant_smoke import _boot


async def measure(
    target: Path, scenario: str, measurement_interval: int = 30, duration: int = 86400
):
    interval = 5
    transport = FakeModbusClient(device_bank())
    with patch(
        "custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient",
        return_value=transport,
    ):
        hass = await _boot(target)
        from homeassistant.helpers.recorder import async_initialize_recorder

        async_initialize_recorder(hass)
        try:
            assert await async_setup_component(
                hass,
                "recorder",
                {
                    "recorder": {
                        "db_url": f"sqlite:///{target}/recorder.db",
                        "commit_interval": 1,
                        "auto_purge": False,
                    }
                },
            )
            flow = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
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
            coordinator = entry.runtime_data.coordinator
            coordinator.update_interval = None
            coordinator.async_set_updated_data(coordinator.data)
            now = 0
            coordinator._monotonic = lambda: now
            coordinator._slow_read_at = None
            coordinator._active_read_at = None
            coordinator._measurement_interval = measurement_interval
            recorder = get_instance(hass)
            await recorder.async_block_till_done()

            def summary():
                with closing(sqlite3.connect(target / "recorder.db")) as conn:
                    rows = dict(
                        conn.execute(
                            "SELECT m.entity_id, count(*) FROM states s JOIN states_meta m USING(metadata_id) GROUP BY m.entity_id"
                        )
                    )
                    attrs = conn.execute(
                        "SELECT count(*) FROM state_attributes"
                    ).fetchone()[0]
                    pages = conn.execute("PRAGMA page_count").fetchone()[0]
                    size = conn.execute("PRAGMA page_size").fetchone()[0]
                return rows, attrs, pages * size

            before, _, _ = summary()
            for now in range(0, duration, interval):
                if scenario != "constant":
                    charging = scenario == "charging" or (
                        scenario == "mixed" and now >= 20 * 3600
                    )
                    connected = charging or scenario == "connected_idle"
                    put(
                        transport.bank,
                        R.EVSE_STATE,
                        5 if charging else (3 if connected else 1),
                    )
                    put(
                        transport.bank,
                        R.CP_STATE,
                        28 if charging else (27 if connected else 10),
                    )
                    # Synthetic fluctuations exceed display precision; all scenarios use the same signal.
                    for i, (voltage, current, power) in enumerate(
                        zip(
                            (R.VOLTAGE_L1, R.VOLTAGE_L2, R.VOLTAGE_L3),
                            (R.CURRENT_L1, R.CURRENT_L2, R.CURRENT_L3),
                            (R.POWER_L1, R.POWER_L2, R.POWER_L3),
                            strict=True,
                        )
                    ):
                        v = 230 + math.sin(now / 37 + i) * 0.8
                        a = 15 + math.sin(now / 23 + i) * 0.15 if charging else 0
                        put(transport.bank, voltage, v)
                        put(transport.bank, current, a)
                        put(transport.bank, power, v * a)
                    put(
                        transport.bank,
                        R.POWER_TOTAL,
                        10350 + math.sin(now / 23) * 80 if charging else 0,
                    )
                    put(transport.bank, R.SESSION_DURATION, now if charging else 0)
                    put(
                        transport.bank,
                        R.SESSION_ENERGY,
                        (now - (20 * 3600 if scenario == "mixed" else 0)) * 11 / 3600
                        if charging
                        else 0,
                    )
                    put(
                        transport.bank,
                        R.ENERGY_TOTAL,
                        1234.5
                        + (now - (20 * 3600 if scenario == "mixed" else 0)) * 11 / 3600
                        if charging
                        else 1234.5,
                    )
                    put(transport.bank, R.TEMPERATURE, 21.5 + math.sin(now / 1800) * 2)
                data = await coordinator._async_update_data()
                coordinator.async_set_updated_data(data)
                await asyncio.sleep(0)
                if now % (interval * 200) == 0:
                    await recorder.async_block_till_done()
            await hass.async_block_till_done()
            await recorder.async_block_till_done()
            after, attrs, size = summary()
            differences = {
                k: n - before.get(k, 0)
                for k, n in after.items()
                if n - before.get(k, 0)
            }
            result = {
                "scenario": scenario,
                "status_interval": interval,
                "measurement_interval": measurement_interval,
                "simulated_seconds": duration,
                "state_rows": sum(differences.values()),
                "rows_by_entity": differences,
                "attributes_rows": attrs,
                "sqlite_bytes": size,
                "measurement": "Synthetic 24h register replay through real HA entities and Recorder, no wallbox hardware; no 24h time/statistics/purge simulation.",
            }
            await hass.config_entries.async_unload(entry.entry_id)
            return result
        finally:
            await hass.async_stop()


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--measurement-interval", type=int, default=30)
    parser.add_argument("--duration", type=int, default=86400)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for scenario in ("constant", "idle", "connected_idle", "mixed", "charging"):
        result = await measure(
            args.output_dir / scenario,
            scenario,
            args.measurement_interval,
            args.duration,
        )
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
        (args.output_dir / f"{scenario}.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    asyncio.run(main())
