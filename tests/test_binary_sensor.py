from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron_rtu.binary_sensor import async_setup_entry
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
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
        values["cp_state"] = 10  # Idle and CP A1 consistently mean unplugged.
        assert sensors["vehicle_connected"].is_on is False
        assert sensors["charging"].is_on is False
        assert sensors["error"].is_on is False
        assert sensors["error"].extra_state_attributes == {"code": 0, "category": "no_error"}

        values["evse_state"] = EvseState.CHARGING
        values["cp_state"] = 28
        values["error_code"] = 200
        values["master_lost_fallback"] = 1
        assert sensors["vehicle_connected"].is_on is True
        assert sensors["charging"].is_on is True
        assert sensors["error"].is_on is True
        # A lost heartbeat shows what the wallbox does about it.
        assert sensors["error"].extra_state_attributes == {
            "code": 200, "category": "energy_manager_unavailable",
            "heartbeat_lost": True, "fallback": "fallback_current", "fallback_current": 6,
        }
        values["ems_fallback_current"] = 1
        assert sensors["error"].extra_state_attributes == {
            "code": 200, "category": "energy_manager_unavailable",
            "heartbeat_lost": True, "fallback": "pause_on_timeout",
        }
        values["ems_fallback_current"] = 3
        assert sensors["error"].extra_state_attributes == {
            "code": 200, "category": "energy_manager_unavailable", "heartbeat_lost": True,
        }
        values["error_code"] = 4711
        values["master_lost_fallback"] = 0
        assert sensors["error"].extra_state_attributes == {"code": 4711, "category": "other"}

    asyncio.run(run())


def test_missing_values_yield_an_unknown_state() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        values = entry.runtime_data.coordinator.data.values
        for key in ("evse_state", "error_code", "ems_fallback_current"):
            values[key] = None
        values["master_lost_fallback"] = 1
        assert sensors["vehicle_connected"].is_on is None
        assert sensors["charging"].is_on is None
        assert sensors["error"].is_on is None
        assert sensors["error"].extra_state_attributes == {"heartbeat_lost": True}
        # Without its register an entity is unavailable instead of unknown.
        assert not sensors["error"].available
        assert sensors["vehicle_connected"].available is False

    asyncio.run(run())


def test_capability_gating_hides_unsupported_binary_sensors() -> None:
    async def run() -> None:
        sensors, _entry = await _sensors(layout=R.LAYOUT_V01_00)
        assert set(sensors) == {"vehicle_connected", "charging", "error", "guest_charging"}
        values = _entry.runtime_data.coordinator.data.values
        values["evse_state"] = EvseState.IDLE
        # Without a pilot register, the wallbox state alone decides.
        assert sensors["vehicle_connected"].is_on is False

    asyncio.run(run())


def test_guest_charging_follows_the_service_and_is_unknown_without_it() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        guest = sensors["guest_charging"]
        assert guest.available
        assert guest.is_on is False
        entry.runtime_data.client.snapshot["control"]["guest_active"] = True
        assert guest.is_on is True
        del entry.runtime_data.client.snapshot["control"]["guest_active"]
        assert guest.is_on is None
        assert not guest.available

    asyncio.run(run())


def test_entities_are_unavailable_after_a_failed_poll() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        entry.runtime_data.coordinator.last_update_success = False
        assert not any(sensor.available for sensor in sensors.values())

    asyncio.run(run())
