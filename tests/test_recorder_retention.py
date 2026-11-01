"""Replay daily charging windows through real statistics, purge and repack.

Only integration/event time is accelerated. This verifies retention mechanics,
not physical disk capacity or a wall-clock soak of the entire HA scheduler.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.tasks import (
    PurgeTask,
    StatisticsTask,
    SynchronizeTask,
)
from homeassistant.helpers.recorder import async_initialize_recorder
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from sqlalchemy import event

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank, put
from tests.recorder_replay import DETAILS, EXTRAS
from tests.test_home_assistant_smoke import _boot


@pytest.mark.parametrize("excluded", [[], DETAILS + EXTRAS], ids=["full", "lean"])
def test_repeated_retention_cycles_keep_history_bounded_and_energy_statistics(
    tmp_path: Path, excluded: list[str]
) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        start = dt_util.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        start += timedelta(days=1)
        timestamp = start.timestamp() + 1
        clock = SimpleNamespace(time=lambda: timestamp)
        with (
            patch("homeassistant.core.time", clock),
            patch("homeassistant.helpers.entity.timer", side_effect=clock.time),
            patch("custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient",
                  return_value=transport),
        ):
            hass = await _boot(tmp_path / "config")
            async_initialize_recorder(hass)
            database = tmp_path / "retention.db"
            vacuums = []
            vacuum_done = asyncio.Event()
            try:
                assert await async_setup_component(hass, "recorder", {
                    "recorder": {
                        "db_url": f"sqlite:///{database}", "commit_interval": 1,
                        "auto_purge": False,
                        "exclude": {"entities": ["sensor.mennekes_amtron_" + key for key in excluded]},
                    }
                })
                flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                result = await hass.config_entries.flow.async_configure(flow["flow_id"], {
                    "port": "/dev/fake", "device_id": 50, "baudrate": 57600,
                    "frame": "8n2", "search": "off",
                })
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                coordinator = entry.runtime_data.coordinator
                coordinator.update_interval = None
                recorder = get_instance(hass)
                await recorder.async_block_till_done()

                def note_sql(_conn, _cursor, statement, _parameters, _context, _many):
                    if statement.strip().upper() == "VACUUM":
                        vacuums.append(statement)
                        hass.loop.call_soon_threadsafe(vacuum_done.set)

                event.listen(recorder.engine, "after_cursor_execute", note_sql)

                async def committed():
                    future = hass.loop.create_future()
                    recorder.queue_task(SynchronizeTask(future))
                    await future

                retained_counts = []
                previous_energy_sum = -1.0
                for day in range(6):
                    period = start + timedelta(days=day)
                    # One hour of changing measurements per simulated day.
                    for sample in range(120):
                        timestamp = period.timestamp() + sample * 30 + 1
                        put(transport.bank, R.ENERGY_TOTAL, 1234.5 + day + sample / 120)
                        put(transport.bank, R.POWER_TOTAL, 1000 + sample)
                        put(transport.bank, R.VOLTAGE_L1, 230 + sample % 3)
                        coordinator._slow_read_at = None
                        coordinator._active_read_at = None
                        coordinator.async_set_updated_data(await coordinator._async_update_data())
                        await hass.async_block_till_done()
                    await committed()
                    for minute in range(0, 60, 5):
                        recorder.queue_task(StatisticsTask(period + timedelta(minutes=minute), False))
                    await committed()
                    if day < 2:
                        continue
                    cutoff = period - timedelta(days=1)
                    vacuum_done.clear()
                    recorder.queue_task(PurgeTask(cutoff, repack=True, apply_filter=False))
                    # Wait through follow-up purge batches and the real VACUUM.
                    await asyncio.wait_for(vacuum_done.wait(), 15)
                    await committed()
                    assert len(vacuums) == day - 1
                    with closing(sqlite3.connect(database)) as conn:
                        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                        assert conn.execute("PRAGMA freelist_count").fetchone() == (0,)
                        for table, query in (
                            ("states", "SELECT COUNT(*), MIN(last_updated_ts) FROM states"),
                            ("statistics_short_term", "SELECT COUNT(*), MIN(start_ts) FROM statistics_short_term"),
                        ):
                            count, oldest = conn.execute(query).fetchone()
                            assert count > 0
                            assert oldest >= cutoff.timestamp()
                            if table == "states":
                                retained_counts.append(count)
                        series, rows = conn.execute(
                            "SELECT COUNT(DISTINCT metadata_id), COUNT(*) FROM statistics"
                        ).fetchone()
                        assert series == 17 - len(excluded)
                        assert rows == series * (day + 1)
                        energy_sum = conn.execute(
                            "SELECT s.sum FROM statistics s JOIN statistics_meta m "
                            "ON m.id=s.metadata_id WHERE m.statistic_id="
                            "'sensor.mennekes_amtron_total_energy' ORDER BY s.start_ts DESC LIMIT 1"
                        ).fetchone()[0]
                        assert energy_sum > previous_energy_sum
                        previous_energy_sum = energy_sum
                # Sliding two-day history stops growing; hourly totals remain.
                assert len(set(retained_counts)) == 1
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 120))
