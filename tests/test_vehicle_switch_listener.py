"""Options applied in place must not hide a vehicle change in the same update."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components import mennekes_amtron_rtu as integration


@pytest.mark.parametrize("applied_by", ["plug_charge", "charging_cost"])
def test_in_place_option_update_still_switches_the_vehicle(monkeypatch, applied_by):
    async def run():
        runtime = SimpleNamespace(
            plug_charge=SimpleNamespace(options_applied=lambda: applied_by == "plug_charge"),
            charging_cost=SimpleNamespace(apply_tariff_only_update=lambda: applied_by == "charging_cost"),
            vehicle_base_options={},
        )
        entry = SimpleNamespace(
            entry_id="entry", data={},
            options={"virtual_plug_charge": True, "active_vehicle_profile": "b" * 32},
            runtime_data=runtime,
        )
        switch = AsyncMock(return_value=True)
        monkeypatch.setattr(integration, "_async_switch_vehicle", switch)
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=AsyncMock()))
        await integration._async_reload_entry_locked(hass, entry)
        # The base settings follow the in-place change, then the vehicle switch
        # runs; neither reloads the entry.
        assert runtime.vehicle_base_options == {"virtual_plug_charge": True}
        switch.assert_awaited_once_with(hass, entry)
        hass.config_entries.async_reload.assert_not_awaited()

    asyncio.run(run())
