"""Each car's phases from its last charge plan its next late start."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu import vehicle_phases as m
from tests.ha_fakes import FakeHass


def test_phases_are_remembered_per_car_and_bounded(tmp_path):
    async def run():
        memory = m.VehiclePhaseMemory(FakeHass(str(tmp_path)), "entry", "serial")
        assert await memory.async_phases("a") is None
        await memory.async_remember("a", 1)
        await memory.async_remember("a", 1)
        await memory.async_remember("b", 3)
        reloaded = m.VehiclePhaseMemory(FakeHass(str(tmp_path)), "entry", "serial")
        assert (await reloaded.async_phases("a"), await reloaded.async_phases("b")) == (1, 3)
        for index in range(m.MAX_VEHICLES):
            await memory.async_remember(str(index), 2)
        # The oldest car drops first; a renewed car moves to the end.
        assert await memory.async_phases("a") is None
        assert await memory.async_phases(str(m.MAX_VEHICLES - 1)) == 2

    asyncio.run(run())


@pytest.mark.parametrize("raw", [
    None, {"phases": []}, {"phases": {"a": 4}}, {"phases": {"a": True}},
    {"phases": {"": 1}}, {"phases": {str(index): 1 for index in range(m.MAX_VEHICLES + 1)}},
])
def test_invalid_saved_phases_are_rejected(raw):
    with pytest.raises(ValueError, match="vehicle phases"):
        m.VehiclePhases.restore(raw)


def test_storage_problems_only_forget_the_phases(tmp_path, caplog):
    async def run():
        memory = m.VehiclePhaseMemory(FakeHass(str(tmp_path)), "entry", "serial")
        with patch.object(m.VehiclePhaseStore, "async_load", side_effect=ValueError("broken")):
            assert await memory.async_phases("a") is None
        with patch.object(m.VehiclePhaseStore, "async_save", side_effect=OSError("full")):
            await memory.async_remember("a", 1)
        assert "Ignoring saved vehicle phases" in caplog.text
        assert "Vehicle phases not remembered" in caplog.text

    asyncio.run(run())
