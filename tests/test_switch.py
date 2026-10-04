from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.switch import async_setup_entry
from tests.fakes import device_bank
from tests.ha_fakes import build_runtime, collect


async def _switches(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry, transport


def test_the_release_and_lock_switches_follow_their_registers() -> None:
    async def run() -> None:
        switches, entry, transport = await _switches()
        assert switches["charging_release"].is_on is True
        assert switches["lock_evse"].is_on is False

        await switches["charging_release"].async_turn_off()
        await switches["lock_evse"].async_turn_on()
        written = dict(transport.writes)
        assert written[R.CHARGING_RELEASE.address] == [0]
        assert written[R.LOCK_EVSE.address] == [1]

        entry.runtime_data.coordinator.data.values["charging_release"] = None
        assert switches["charging_release"].is_on is None
        entry.runtime_data.coordinator.data.values["lock_evse"] = None
        assert switches["lock_evse"].is_on is None

    asyncio.run(run())


def test_the_release_switch_can_be_turned_on_again() -> None:
    async def run() -> None:
        switches, _entry, transport = await _switches()
        await switches["charging_release"].async_turn_on()
        await switches["lock_evse"].async_turn_off()
        written = dict(transport.writes)
        assert written[R.CHARGING_RELEASE.address] == [1]
        assert written[R.LOCK_EVSE.address] == [0]

    asyncio.run(run())


def test_the_pause_switch_uses_the_documented_pause_value() -> None:
    async def run() -> None:
        switches, entry, transport = await _switches()
        pause = switches["charging_paused"]
        assert pause.is_on is False
        await pause.async_turn_on()
        assert any(
            address == R.CHARGING_CURRENT_EMS.address
            for address, _ in transport.writes
        )
        entry.runtime_data.coordinator.data.values["charging_current_ems"] = 1.0
        assert pause.is_on is True

    asyncio.run(run())


def test_resuming_restores_charging() -> None:
    async def run() -> None:
        switches, entry, transport = await _switches(
            bank=device_bank(charging_current=1.0)
        )
        entry.runtime_data.control.charging_setpoint = 12.0
        await switches["charging_paused"].async_turn_off()
        assert any(
            address == R.CHARGING_CURRENT_EMS.address
            for address, _ in transport.writes
        )

    asyncio.run(run())
