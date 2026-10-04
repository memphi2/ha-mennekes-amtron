from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.select import async_setup_entry
from tests.ha_fakes import build_runtime, collect


async def _selects(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry, transport


def test_the_solar_mode_reflects_and_writes_the_register() -> None:
    async def run() -> None:
        selects, _entry, transport = await _selects()
        solar = selects["solar_charging_mode"]
        assert solar.current_option == "sunshine"
        assert solar.options == ["not_active", "fast", "sunshine", "sunshine_plus"]
        await solar.async_select_option("sunshine_plus")
        assert dict(transport.writes)[R.SOLAR_CHARGING_MODE.address] == [3]

    asyncio.run(run())


def test_the_phase_select_exists_only_on_capable_hardware() -> None:
    async def run() -> None:
        selects, _entry, transport = await _selects()
        assert "requested_phases" in selects
        await selects["requested_phases"].async_select_option("single_phase")
        assert dict(transport.writes)[R.REQUESTED_PHASES.address] == [1]

        gated, _entry2, _transport2 = await _selects(phase_options=1)
        assert "requested_phases" not in gated
        assert "solar_charging_mode" in gated

    asyncio.run(run())


def test_unknown_register_values_yield_no_option() -> None:
    async def run() -> None:
        selects, entry, _transport = await _selects()
        values = entry.runtime_data.coordinator.data.values
        values["solar_charging_mode"] = None
        values["requested_phases"] = 9
        assert selects["solar_charging_mode"].current_option is None
        assert selects["requested_phases"].current_option is None

    asyncio.run(run())
