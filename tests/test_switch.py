from __future__ import annotations

import asyncio
import logging

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
from custom_components.mennekes_amtron_rtu.unplug_rule import UnplugRule
from tests.fakes import device_bank, put, raise_connection_error
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


def _plug(transport, connected: bool) -> None:
    transport.bank[R.EVSE_STATE.address] = 2 if connected else 1
    transport.bank[R.CP_STATE.address] = 11 if connected else 10


async def _unplug_rule(transport, entry, *, release: int = 1) -> UnplugRule:
    transport.bank[R.CHARGING_RELEASE.address] = release
    _plug(transport, True)
    await entry.runtime_data.coordinator.async_refresh()
    rule = UnplugRule(entry.runtime_data.coordinator.hass, entry)
    transport.writes.clear()
    return rule


async def _unplug(transport, entry) -> None:
    coordinator = entry.runtime_data.coordinator
    # An unknown reading neither counts as a disconnect nor forgets the plug.
    coordinator.data.values["evse_state"] = None
    coordinator.async_update_listeners()
    _plug(transport, False)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await asyncio.gather(*entry.background_tasks)


@pytest.mark.parametrize(("plug_charge", "release", "expected"), [
    (False, 1, True), (True, 1, False), (False, 0, False),
])
def test_release_is_switched_off_after_a_confirmed_unplug(monkeypatch, plug_charge, release, expected) -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        # With the gate enabled, the gate already owns the release.
        monkeypatch.setattr(
            type(entry.runtime_data.control), "plug_charge", property(lambda _self: {"enabled": plug_charge}),
        )
        rule = await _unplug_rule(transport, entry, release=release)
        await _unplug(transport, entry)
        released = [words for address, words in transport.writes if address == R.CHARGING_RELEASE.address]
        assert released == ([[0]] if expected else [])
        rule.stop()
        rule.stop()

    asyncio.run(run())


def test_the_rule_does_nothing_without_write_access_or_after_stopping() -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        rule = await _unplug_rule(transport, entry)
        rule.stop()
        await _unplug(transport, entry)
        assert transport.writes == []

        rule = await _unplug_rule(transport, entry)
        entry.runtime_data.client.snapshot["control"]["read_only"] = True
        entry.runtime_data.coordinator.data.values.update(evse_state=1, cp_state=10)
        entry.runtime_data.coordinator.async_update_listeners()
        assert entry.background_tasks == []
        rule.stop()

    asyncio.run(run())


def test_a_failed_poll_is_no_disconnect() -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        rule = await _unplug_rule(transport, entry)
        coordinator = entry.runtime_data.coordinator
        coordinator.last_update_success = False
        coordinator.async_update_listeners()
        coordinator.last_update_success = True
        coordinator.async_update_listeners()
        assert entry.background_tasks == []
        rule.stop()

    asyncio.run(run())


@pytest.mark.parametrize("error", [HomeAssistantError("busy"), OSError("port closed")])
def test_a_failed_release_off_is_logged_not_raised(monkeypatch, caplog, error) -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()

        async def release_off(_released: bool) -> None:
            raise error

        monkeypatch.setattr(entry.runtime_data.control, "async_set_charging_release", release_off)
        rule = await _unplug_rule(transport, entry)
        await _unplug(transport, entry)
        assert "Control: charging release not switched off after unplugging" in caplog.text
        rule.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("release", "paused", "goal_stop", "resumed"), [
    (1, True, False, True), (0, True, False, True),
    (1, True, True, False), (1, False, False, False),
])
def test_unplugging_also_ends_a_manual_pause(monkeypatch, release, paused, goal_stop, resumed) -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        control = entry.runtime_data.control
        calls: list[str] = []

        async def resume(**_kwargs: object) -> None:
            calls.append("resume")

        async def release_off(released: bool) -> None:
            calls.append(f"release {released}")

        monkeypatch.setattr(control, "async_resume_charging", resume)
        monkeypatch.setattr(control, "async_set_charging_release", release_off)
        monkeypatch.setattr(type(control), "energy_goal_stop_requested", property(lambda _self: goal_stop))
        monkeypatch.setattr(type(control), "pause_requested", property(lambda _self: paused))
        rule = await _unplug_rule(transport, entry, release=release)
        await _unplug(transport, entry)
        assert calls == ["release False"] * release + ["resume"] * int(resumed)
        rule.stop()

    asyncio.run(run())


def _records(caplog, text: str) -> list[int]:
    return [record.levelno for record in caplog.records if text in record.getMessage()]


async def _poll(transport, entry) -> None:
    """A poll in which some reading moved, so the coordinator notifies."""
    words = [transport.bank[R.MAX_CURRENT_HOUSE.address + offset] for offset in range(2)]
    put(transport.bank, R.MAX_CURRENT_HOUSE, decode_register(R.MAX_CURRENT_HOUSE, words) - 0.5)
    await entry.runtime_data.coordinator.async_refresh()


def test_a_failed_release_off_is_retried_with_the_next_poll_and_reported_once(
    monkeypatch, caplog,
) -> None:
    async def run() -> None:
        caplog.set_level(logging.DEBUG, logger="custom_components.mennekes_amtron_rtu.unplug_rule")
        _hass, entry, transport = await build_runtime()
        calls: list[bool] = []

        async def release_off(released: bool) -> None:
            calls.append(released)
            if len(calls) < 3:
                raise HomeAssistantError("busy")

        monkeypatch.setattr(entry.runtime_data.control, "async_set_charging_release", release_off)
        rule = await _unplug_rule(transport, entry)
        await _unplug(transport, entry)
        assert calls == [False]
        for expected in (2, 3, 3):
            await _poll(transport, entry)
            await asyncio.gather(*entry.background_tasks)
            assert len(calls) == expected
        failed = "Control: charging release not switched off after unplugging; retrying with the next poll"
        assert _records(caplog, failed) == [logging.WARNING, logging.DEBUG]
        assert _records(caplog, "Control: charging release switched off after unplugging") == [logging.INFO]
        rule.stop()

    asyncio.run(run())


def test_a_retry_waits_for_the_running_switch_off_and_ends_when_the_car_is_back(monkeypatch) -> None:
    async def run() -> None:
        _hass, entry, transport = await build_runtime()
        gate = asyncio.Event()
        calls: list[bool] = []

        async def release_off(released: bool) -> None:
            calls.append(released)
            if len(calls) == 2:
                await gate.wait()
            raise HomeAssistantError("busy")

        monkeypatch.setattr(entry.runtime_data.control, "async_set_charging_release", release_off)
        rule = await _unplug_rule(transport, entry)
        await _unplug(transport, entry)
        assert calls == [False]
        await _poll(transport, entry)
        await asyncio.sleep(0)
        assert len(calls) == 2
        # Polls while the switch-off still runs start no second one.
        await _poll(transport, entry)
        await _poll(transport, entry)
        await asyncio.sleep(0)
        assert len(calls) == 2
        gate.set()
        await asyncio.gather(*entry.background_tasks)
        # The car is back: nothing is owed any more.
        _plug(transport, True)
        await _poll(transport, entry)
        await _poll(transport, entry)
        await asyncio.gather(*entry.background_tasks)
        assert len(calls) == 2
        rule.stop()

    asyncio.run(run())
