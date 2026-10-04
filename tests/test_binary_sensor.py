from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.binary_sensor import async_setup_entry
from custom_components.mennekes_amtron.enums import EvseState
from tests.ha_fakes import FakeHass, build_runtime, collect


async def _sensors(hass: FakeHass | None = None, **kwargs: object):
    hass, entry, _transport = await build_runtime(hass=hass, **kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry


def test_states_are_derived_from_the_registers() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        values = entry.runtime_data.coordinator.data.values
        assert sensors["vehicle_connected"].is_on is False
        assert sensors["charging"].is_on is False
        assert sensors["downgrade_active"].is_on is False
        assert sensors["error"].is_on is False
        assert sensors["master_lost_fallback"].is_on is False
        assert sensors["authorization_enabled"].is_on is False
        assert sensors["grid_imbalance_enabled"].is_on is True
        assert sensors["cable_lock_enabled"].is_on is False
        assert sensors["phase_switch_capable"].is_on is True

        values["evse_state"] = EvseState.CHARGING
        values["downgrade_status"] = 2
        values["error_code"] = 200
        assert sensors["vehicle_connected"].is_on is True
        assert sensors["charging"].is_on is True
        assert sensors["downgrade_active"].is_on is True
        assert sensors["error"].is_on is True

    asyncio.run(run())


def test_missing_values_yield_an_unknown_state() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        values = entry.runtime_data.coordinator.data.values
        for key in ("evse_state", "downgrade_status", "error_code", "grid_imbalance"):
            values[key] = None
        assert sensors["vehicle_connected"].is_on is None
        assert sensors["charging"].is_on is None
        assert sensors["downgrade_active"].is_on is None
        assert sensors["error"].is_on is None
        assert sensors["grid_imbalance_enabled"].is_on is None

    asyncio.run(run())


def test_capability_gating_hides_unsupported_binary_sensors() -> None:
    async def run() -> None:
        sensors, _entry = await _sensors(layout=R.LAYOUT_V01_00)
        assert "master_lost_fallback" not in sensors
        assert "phase_switch_capable" not in sensors
        assert "vehicle_connected" in sensors

    asyncio.run(run())
