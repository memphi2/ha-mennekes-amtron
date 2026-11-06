"""Compile real Recorder statistics from RTU sensors in disposable SQLite."""

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
from homeassistant.components.recorder.tasks import StatisticsTask
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import async_initialize_recorder
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from tests.fakes import FakeModbusClient, device_bank, put
from tests.recorder_replay import (
    DEFAULT_STATISTICS,
    DETAILS,
    EXTRAS,
    OPTIONAL_MEASUREMENTS,
)
from tests.test_home_assistant_smoke import _boot

PREFIX = "sensor.mennekes_amtron_"


@pytest.mark.parametrize("excluded", [[], DETAILS, DETAILS + EXTRAS])
def test_hourly_statistics_and_recorder_exclusions(
    tmp_path: Path, excluded: list[str]
) -> None:
    """Keep energy sums and power means while excluded sensors remain live."""

    async def run() -> None:
        serial = "ABC123456789"
        transport = FakeModbusClient(device_bank(serial=serial))
        # Explicit event timestamps avoid dependence on the wall clock or on
        # crossing an hour boundary. Recorder itself and its compiler are real.
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        start += timedelta(hours=1)
        timestamp = start.timestamp() + 1
        clock = SimpleNamespace(time=lambda: timestamp)
        with (
            patch("homeassistant.core.time", clock),
            patch("homeassistant.helpers.entity.timer", side_effect=clock.time),
            patch(
                "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
                return_value=transport,
            ),
        ):
            hass = await _boot(tmp_path / "config")
            # Preserve the user's previously enabled detail readings. This
            # scenario tests Recorder exclusion, not the new-install defaults.
            registry = er.async_get(hass)
            for key in OPTIONAL_MEASUREMENTS:
                registry.async_get_or_create(
                    "sensor", DOMAIN, f"{serial}_{key}",
                    suggested_object_id=f"mennekes_amtron_{key}", disabled_by=None,
                )
            async_initialize_recorder(hass)
            database = tmp_path / "recorder.db"
            try:
                assert await async_setup_component(
                    hass,
                    "recorder",
                    {
                        "recorder": {
                            "db_url": f"sqlite:///{database}",
                            "commit_interval": 1,
                            "auto_purge": False,
                            "exclude": {"entities": [PREFIX + key for key in excluded]},
                        }
                    },
                )
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial"})
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
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                coordinator = entry.runtime_data.coordinator
                coordinator.update_interval = None
                recorder = get_instance(hass)
                await recorder.async_block_till_done()
                timestamp += 150
                put(transport.bank, R.ENERGY_TOTAL, 1234.75)
                put(transport.bank, R.POWER_TOTAL, 1000.0)
                coordinator._poller._slow_read_at = None
                coordinator._poller._active_read_at = None
                coordinator.async_set_updated_data(
                    await coordinator._async_update_data()
                )
                await hass.async_block_till_done()
                await recorder.async_block_till_done()
                for minute in range(0, 60, 5):
                    recorder.queue_task(
                        StatisticsTask(start + timedelta(minutes=minute), False)
                    )
                await recorder.async_block_till_done()
                with closing(sqlite3.connect(database)) as conn:
                    rows = conn.execute(
                        "SELECT m.statistic_id, m.unit_of_measurement, m.unit_class, "
                        "m.mean_type, s.mean, s.state, s.sum "
                        "FROM statistics s JOIN statistics_meta m ON m.id=s.metadata_id"
                    ).fetchall()
                    recorded = {
                        row[0]
                        for row in conn.execute(
                            "SELECT DISTINCT m.entity_id FROM states s "
                            "JOIN states_meta m ON m.metadata_id=s.metadata_id"
                        )
                    }
                stats = {row[0]: row[1:] for row in rows}
                expected_statistics = {
                    PREFIX + key
                    for key in (DEFAULT_STATISTICS | set(OPTIONAL_MEASUREMENTS)).difference(excluded)
                }
                assert set(stats) == expected_statistics
                assert len(rows) == len(stats)
                duration = hass.states.get(PREFIX + "session_duration")
                assert duration is not None
                assert "state_class" not in duration.attributes
                assert PREFIX + "session_duration" not in stats
                energy = stats[PREFIX + "total_energy"]
                assert energy[:3] == ("kWh", "energy", 0)
                assert energy[3] is None
                assert energy[4:] == pytest.approx((1234.75, 0.25))
                power = stats[PREFIX + "total_power"]
                assert power[:3] == ("W", "power", 1)
                assert 0 < power[3] <= 1000
                assert power[4:] == (None, None)
                for key in excluded:
                    entity_id = PREFIX + key
                    assert entity_id not in recorded
                    assert entity_id not in stats
                    assert hass.states.get(entity_id) is not None
                assert await hass.config_entries.async_unload(entry.entry_id)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
