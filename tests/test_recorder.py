"""Check actual SQLite writes, including HA's unchanged-state suppression."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.recorder_replay import DETAILS, EXTRAS, measure


def test_unchanged_values_do_not_create_recorder_rows(tmp_path: Path) -> None:
    result = asyncio.run(measure(tmp_path / "constant", "constant", duration=300))
    assert result["state_rows"] == 0


def test_measurement_interval_reduces_real_recorder_writes(tmp_path: Path) -> None:
    async def run() -> None:
        fast = await measure(tmp_path / "fast", "charging", 5, duration=300)
        default = await measure(tmp_path / "default", "charging", 30, duration=300)
        lean = await measure(tmp_path / "lean", "charging", 30, duration=300, profile="lean")
        assert 0 < default["state_rows"] < fast["state_rows"] * 0.3
        # The voltages under load are attributes of the total power: a row per
        # distinct whole-volt combination, so fewer samples cannot need more.
        assert 0 < default["attributes_rows"] <= fast["attributes_rows"]
        assert default["rows_by_entity"]["sensor.mennekes_amtron_total_energy"] > 0
        assert default["rows_by_entity"]["sensor.mennekes_amtron_total_power"] > 0
        assert 0 < lean["state_rows"] < default["state_rows"]
        excluded_ids = {f"sensor.mennekes_amtron_{key}" for key in DETAILS + EXTRAS}
        # The lean profile removes exactly the requested readings; a percentage
        # tied to the former always-enabled detail entities is no longer useful.
        assert lean["rows_by_entity"] == {
            entity_id: count
            for entity_id, count in default["rows_by_entity"].items()
            if entity_id not in excluded_ids
        }
        for key in ("total_energy", "total_power"):
            entity_id = f"sensor.mennekes_amtron_{key}"
            assert lean["rows_by_entity"][entity_id] == default["rows_by_entity"][entity_id]

    asyncio.run(run())
