from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import (
    CONTROL_PLATFORMS,
    READ_PLATFORMS,
    _entry_platforms,
)
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.switch import async_setup_entry
from tests.fakes import device_bank, raise_connection_error
from tests.ha_fakes import build_runtime, collect


async def _switches(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry, transport


def _currents(transport) -> list[float]:
    return [
        decode_register(R.CHARGING_CURRENT_EMS, words)
        for address, words in transport.writes
        if address == R.CHARGING_CURRENT_EMS.address
    ]


def test_the_release_and_lock_switches_follow_their_registers() -> None:
    async def run() -> None:
        switches, entry, transport = await _switches()
        device = entry.service.control
        control = entry.runtime_data.control
        clock = [1000.0]
        device._monotonic = lambda: clock[0]

        async def sleep(seconds: float) -> None:
            clock[0] += seconds
            await asyncio.sleep(0)

        device._sleep = sleep
        assert switches["charging_release"].is_on is True
        assert switches["lock_evse"].is_on is False

        await switches["charging_release"].async_turn_off()
        await switches["lock_evse"].async_turn_on()
        # The second change waits out the state-change interval in the service.
        assert control.pending_action == "lock"
        assert transport.bank[R.LOCK_EVSE.address] == 0
        assert switches["lock_evse"].is_on is False
        await asyncio.wait_for(device._flush_task, 1)
        await entry.runtime_data.coordinator.async_refresh()
        assert control.pending_action is None
        written = dict(transport.writes)
        assert written[R.CHARGING_RELEASE.address] == [0]
        assert written[R.LOCK_EVSE.address] == [1]
        assert switches["charging_release"].is_on is False
        assert switches["lock_evse"].is_on is True

        entry.runtime_data.coordinator.data.values["charging_release"] = None
        assert switches["charging_release"].is_on is None
        entry.runtime_data.coordinator.data.values["lock_evse"] = None
        assert switches["lock_evse"].is_on is None
        await device.async_shutdown()

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
        assert _currents(transport) == [1.0]
        assert pause.is_on is True
        assert entry.runtime_data.control.pause_requested is True

    asyncio.run(run())


def test_resuming_restores_charging() -> None:
    async def run() -> None:
        switches, entry, transport = await _switches(
            bank=device_bank(charging_current=1.0)
        )
        entry.service.control.charging_setpoint = 12.0
        assert switches["charging_paused"].is_on is True
        await switches["charging_paused"].async_turn_off()
        assert entry.runtime_data.control.pause_requested is False
        assert _currents(transport) == [12.0]
        assert switches["charging_paused"].is_on is False

    asyncio.run(run())


def test_an_unknown_current_leaves_the_pause_state_unknown() -> None:
    async def run() -> None:
        switches, entry, _transport = await _switches()
        entry.runtime_data.client.snapshot["values"]["charging_current_ems"] = None
        assert switches["charging_paused"].is_on is None

    asyncio.run(run())


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("failure", ["lost_reply", "cancelled"])
def test_failed_release_commands_cannot_erase_a_block(released, failure):
    async def run():
        _hass, entry, transport = await build_runtime()
        device = entry.service.control
        control = entry.runtime_data.control
        device.charging_release_requested = False if released else None
        transport.write_exceptions[R.CHARGING_RELEASE.address] = (
            asyncio.CancelledError()
            if failure == "cancelled"
            else raise_connection_error()
        )
        with pytest.raises(
            asyncio.CancelledError if failure == "cancelled" else HomeAssistantError
        ):
            await control.async_set_charging_release(released)
        assert device.charging_release_requested is False
        await device.async_shutdown()

    asyncio.run(run())


def test_read_only_entries_offer_no_control_platforms() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime(read_only=True)
        assert not entry.runtime_data.controls
        assert _entry_platforms(entry) == list(READ_PLATFORMS)
        _hass, entry, _transport = await build_runtime()
        assert _entry_platforms(entry) == [*READ_PLATFORMS, *CONTROL_PLATFORMS]

    asyncio.run(run())


def test_controls_are_unavailable_while_the_service_allows_no_writes() -> None:
    async def run() -> None:
        switches, entry, _transport = await _switches()
        assert all(switches[key].available for key in ("charging_paused", "charging_release", "lock_evse"))
        entry.runtime_data.client.snapshot["control"]["read_only"] = True
        assert not any(switches[key].available for key in ("charging_paused", "charging_release", "lock_evse"))
        with pytest.raises(ServiceValidationError):
            await switches["lock_evse"].async_turn_on()

    asyncio.run(run())


class _Gate:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def async_set_enabled(self, enabled: bool) -> None:
        self.calls.append(enabled)


def test_the_plug_and_charge_switch_follows_the_service_gate() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        gate = _Gate()
        entry.runtime_data.plug_charge = gate
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
        switch = added["virtual_plug_charge"]
        snapshot = entry.runtime_data.client.snapshot["control"]
        snapshot["plug_charge"] = {"enabled": True}
        assert switch.is_on is True
        snapshot["read_only"] = True
        # Switching the gate off stays possible without write access.
        assert switch.available
        snapshot["plug_charge"] = {"enabled": False}
        assert switch.is_on is False
        assert not switch.available
        await switch.async_turn_on()
        await switch.async_turn_off()
        assert gate.calls == [True, False]

        entry.runtime_data.plug_charge = None
        with pytest.raises(ServiceValidationError) as error:
            await switch.async_turn_on()
        assert error.value.translation_key == "plug_charge_unavailable"

    asyncio.run(run())


def test_without_plug_and_charge_there_is_no_gate_switch() -> None:
    switches, _entry, _transport = asyncio.run(_switches())
    assert set(switches) == {"charging_paused", "charging_release", "lock_evse"}
