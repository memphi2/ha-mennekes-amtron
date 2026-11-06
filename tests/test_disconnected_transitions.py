"""Fresh unplugged status exempts transitions, never their register write floors."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.fakes import put, raise_connection_error
from tests.test_control import Harness
from tests.test_modbus_service import current_writes, service_fixture


def _unplug(harness, cp=CpState.A1):
    put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
    put(harness.transport.bank, R.CP_STATE, cp)


@pytest.mark.parametrize("cp", [CpState.A1, CpState.A2])
@pytest.mark.parametrize("action", ["pause", "resume", "current", "release_on", "release_off", "lock", "unlock", "phases"])
def test_disconnected_commands_skip_long_guard_using_live_status(action, cp):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        _unplug(harness, cp)
        # The cache deliberately disagrees; only live device readings qualify.
        harness.snapshot.values["evse_state"] = EvseState.CHARGING
        control._write_state.mode_change_at = harness.clock[0]
        if action in ("resume", "current"):
            control._write_state.confirmed_current = 1
            control.pause_requested = True
        if action == "release_on":
            put(harness.transport.bank, R.CHARGING_RELEASE, 0)
        if action == "unlock":
            put(harness.transport.bank, R.LOCK_EVSE, 1)
        calls = {
            "pause": control.async_pause_charging,
            "resume": control.async_resume_charging,
            "current": lambda: control.async_set_charging_current(8),
            "release_on": lambda: control.async_set_charging_release(True),
            "release_off": lambda: control.async_set_charging_release(False),
            "lock": lambda: control.async_set_lock_evse(True),
            "unlock": lambda: control.async_set_lock_evse(False),
            "phases": lambda: control.async_set_requested_phases(1),
        }
        await calls[action]()
        assert len(harness.transport.writes) == 1
        assert control.pending_action is None
        assert harness.clock[0] == 1000
        assert (R.EVSE_STATE.address, 1) in harness.transport.reads
        assert (R.CP_STATE.address, 1) in harness.transport.reads
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["connected", "error", "invalid_evse", "evse_read", "cp_read", "cp_connected", "cp_invalid", "cp_init", "legacy"])
def test_unproven_absence_keeps_guard_even_when_cache_says_idle(failure):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        _unplug(harness)
        harness.snapshot.values.update(evse_state=EvseState.IDLE, cp_state=CpState.A1)
        control._write_state.mode_change_at = harness.clock[0]
        if failure in ("connected", "error", "invalid_evse"):
            put(harness.transport.bank, R.EVSE_STATE, {"connected": 2, "error": 6, "invalid_evse": 99}[failure])
        elif failure in ("evse_read", "cp_read"):
            spec = R.EVSE_STATE if failure == "evse_read" else R.CP_STATE
            harness.transport.read_exceptions[spec.address] = raise_connection_error()
        elif failure == "legacy":
            harness.identity.layout_version = R.LAYOUT_V01_01
            put(harness.transport.bank, R.MODBUS_LAYOUT_VERSION, R.LAYOUT_V01_01)
        else:
            put(harness.transport.bank, R.CP_STATE, {"cp_connected": 11, "cp_invalid": 99, "cp_init": 0}[failure])
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        assert harness.transport.writes == []
        if failure == "legacy":
            assert all(address != R.CP_STATE.address for address, _ in harness.transport.reads)
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("phases", [False, True])
def test_unplugged_write_floor_remains_and_replug_restores_long_guard(phases):
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        _unplug(harness)
        if phases:
            await control.async_set_requested_phases(1)
            await control.async_set_requested_phases(0)
        else:
            await control.async_pause_charging()
            await control.async_resume_charging()
        assert len(harness.transport.writes) == 1
        assert control.pending_action is not None
        put(harness.transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)
        put(harness.transport.bank, R.CP_STATE, CpState.B1)
        # Keep the car connected until the interval ends; phase switching then
        # still needs its separate pause and grace time.
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == (1305 if phases else 1300)
        assert control.pending_action is None

    asyncio.run(run())


def test_unplugged_current_writes_still_wait_five_seconds():
    async def run():
        harness = Harness(state_change_interval_seconds=300)
        control = await harness.connect()
        _unplug(harness)
        await control.async_pause_charging()
        await control.async_resume_charging()
        assert len(harness.transport.writes) == 1
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1005
        assert len(harness.transport.writes) == 2

    asyncio.run(run())


def test_service_commands_use_the_same_unplugged_exception(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        try:
            put(transport.bank, R.EVSE_STATE, EvseState.IDLE)
            put(transport.bank, R.CP_STATE, CpState.A1)
            for command in ("pause", "resume"):
                await service.command({
                    "command": command,
                    "expected_serial_number": service.identity.serial_number,
                })
                assert service.control.pending_action is None
                clock[0] += 5
            assert current_writes(transport) == [1, 8]
            assert service.heartbeat.running
        finally:
            await service.stop()

    asyncio.run(run())


def test_unplugging_releases_queued_transition_without_timestamp_churn():
    async def run():
        harness = Harness(state_change_interval_seconds=300, values={"evse_state": EvseState.EV_CONNECTED})
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        deadline = control.pending_until
        refreshes = harness.refreshes

        async def disconnect(delay):
            assert control.pending_until == deadline
            assert harness.refreshes == refreshes
            await harness._sleep(delay)
            if harness.clock[0] >= 1010:
                _unplug(harness)

        control._sleep = disconnect
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1010
        assert control.pending_action is None
        assert len(harness.transport.writes) == 2

    asyncio.run(run())


@pytest.mark.parametrize("confirm_first", [False, True])
def test_unplugging_skips_remaining_phase_confirmation_and_grace(confirm_first):
    async def run():
        harness = Harness(phase_switch_grace_seconds=30, values={"evse_state": EvseState.EV_CONNECTED if confirm_first else EvseState.CHARGING})
        control = await harness.connect()
        put(harness.transport.bank, R.CP_STATE, CpState.B1 if confirm_first else CpState.C2)

        async def disconnect(delay):
            await harness._sleep(delay)
            _unplug(harness)

        control._sleep = disconnect
        await control.async_set_requested_phases(1)
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == (1005 if confirm_first else 1001)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert control.pause_requested is True

    asyncio.run(run())


@pytest.mark.parametrize("cp", [CpState.B1, CpState.C2, CpState.E])
def test_idle_with_conflicting_cp_never_skips_phase_preparation(cp):
    async def run():
        harness = Harness()
        control = await harness.connect()
        _unplug(harness, cp)
        with pytest.raises(ServiceValidationError):
            await control.async_set_requested_phases(1)
        assert harness.transport.writes == []

    asyncio.run(run())
