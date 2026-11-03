from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoredExtraData

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.switch import async_setup_entry
from tests.fakes import device_bank, raise_connection_error
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
            address == R.CHARGING_CURRENT_EMS.address for address, _ in transport.writes
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
            address == R.CHARGING_CURRENT_EMS.address for address, _ in transport.writes
        )

    asyncio.run(run())


@pytest.mark.parametrize("requested", [False, True, None])
def test_release_intent_is_restored_separately_from_fault_readback(requested):
    async def run():
        switches, entry, _transport = await _switches()
        switch = switches["charging_release"]

        async def extra():
            return RestoredExtraData({"charging_release_requested": requested})

        switch.async_get_last_extra_data = extra
        await switch.async_added_to_hass()
        assert entry.runtime_data.control.charging_release_requested is requested
        assert switch.extra_restore_state_data.as_dict() == {
            "charging_release_requested": requested
        }

    asyncio.run(run())


@pytest.mark.parametrize("old_state", [None, "off", "on", "unavailable"])
@pytest.mark.parametrize("extra_value", ["absent", "invalid", "missing_key"])
def test_upgrades_preserve_a_previously_disabled_release(old_state, extra_value):
    async def run():
        switches, entry, _transport = await _switches()
        switch = switches["charging_release"]

        async def extra():
            if extra_value == "missing_key":
                return RestoredExtraData({})
            return (
                None
                if extra_value == "absent"
                else RestoredExtraData({"charging_release_requested": "bad"})
            )

        async def state():
            return None if old_state is None else SimpleNamespace(state=old_state)

        switch.async_get_last_extra_data = extra
        switch.async_get_last_state = state
        await switch.async_added_to_hass()
        assert entry.runtime_data.control.charging_release_requested is (
            False if old_state == "off" else None
        )

    asyncio.run(run())


def test_reload_does_not_override_current_release_intent():
    async def run():
        switches, entry, _transport = await _switches()
        entry.runtime_data.control.charging_release_requested = False
        await switches["charging_release"].async_added_to_hass()
        assert entry.runtime_data.control.charging_release_requested is False

    asyncio.run(run())


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("failure", ["lost_reply", "cancelled"])
def test_failed_release_commands_cannot_erase_a_block(released, failure):
    async def run():
        switches, entry, transport = await _switches()
        control = entry.runtime_data.control
        control.charging_release_requested = False if released else None
        transport.write_exceptions[R.CHARGING_RELEASE.address] = (
            asyncio.CancelledError()
            if failure == "cancelled"
            else raise_connection_error()
        )
        with pytest.raises(
            asyncio.CancelledError if failure == "cancelled" else HomeAssistantError
        ):
            await control.async_set_charging_release(released)
        assert control.charging_release_requested is False
        assert switches["charging_release"].extra_restore_state_data.as_dict() == {
            "charging_release_requested": False
        }
        await control.async_shutdown()
        await entry.runtime_data.client.async_close()

    asyncio.run(run())
