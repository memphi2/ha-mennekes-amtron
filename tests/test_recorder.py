"""Check actual SQLite writes, including HA's unchanged-state suppression."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.recorder_replay import measure


def test_unchanged_values_do_not_create_recorder_rows(tmp_path: Path) -> None:
    result = asyncio.run(measure(tmp_path / "constant", "constant", duration=300))
    assert result["state_rows"] == 0


def test_measurement_interval_reduces_real_recorder_writes(tmp_path: Path) -> None:
    async def run() -> None:
        fast = await measure(tmp_path / "fast", "charging", 5, duration=300)
        default = await measure(tmp_path / "default", "charging", 30, duration=300)
        assert 0 < default["state_rows"] < fast["state_rows"] * 0.3
        assert default["attributes_rows"] == fast["attributes_rows"]
        assert default["rows_by_entity"]["sensor.mennekes_amtron_total_energy"] > 0
        assert default["rows_by_entity"]["sensor.mennekes_amtron_total_power"] > 0

    asyncio.run(run())
