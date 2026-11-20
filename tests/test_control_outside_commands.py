"""Writes outside a command, the cap and restored state around a waiting stop."""

import asyncio
import struct

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.plug_charge import PlugChargeState
from custom_components.mennekes_amtron_rtu.device.plug_cycle import PlugCycle
from tests.fakes import raise_connection_error
from tests.test_control import Harness
from tests.test_modbus_service import service_fixture
from tests.test_unplug_release import _plug, _poll, _rule


def events(control):
    return [
        (e["outcome"], e["action"], e.get("reason"))
        for e in control.events.snapshot()["events"]
    ]


async def charging(**options):
    h = Harness(
        state_change_interval_seconds=300,
        values={"charging_current_ems": 16.0, "evse_state": EvseState.CHARGING},
        **options,
    )
    control = await h.connect()
    h.transport.bank[R.EVSE_STATE.address] = int(EvseState.CHARGING)
    h.transport.bank[R.CP_STATE.address] = int(CpState.C2)
    return h, control


def currents(h):
    return [
        round(struct.unpack(">f", struct.pack(">HH", *w))[0], 2)
        for w in h.written(R.CHARGING_CURRENT_EMS)
    ]


def test_closing_the_release_after_an_unplug_keeps_a_waiting_lock(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path)
        control = service.control
        control.write_state.mode_change_at = clock[0]
        await control.async_set_lock_evse(True)
        assert control.pending_action == "lock"
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert control.pending_action == "lock"
        _plug(transport, False)
        await _poll(service)
        assert control.pending_action == "lock"
        await service.stop()

    asyncio.run(run())


def test_closing_the_release_after_an_unplug_keeps_a_waiting_pause(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path)
        control = service.control
        control.write_state.mode_change_at = clock[0]
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert control.pending_action == "pause"
        _plug(transport, False)
        await _poll(service)
        assert control.pending_action == "pause" or control.pause_requested is True
        await service.stop()

    asyncio.run(run())


def test_an_uncertain_recovery_write_keeps_a_waiting_pause():
    async def run():
        h, control = await charging(error_code=200)
        h.transport.bank[R.CHARGING_RELEASE.address] = 1
        h.client.write_state.mode_change_at = h.clock[0]
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        h.clock[0] += 10
        h.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = (
            raise_connection_error()
        )
        with pytest.raises(DeviceError):
            await control.async_recover_from_error()
        assert control.pending_action == "pause"
        await control.async_shutdown()

    asyncio.run(run())


def test_a_lowered_cap_waits_behind_a_waiting_stop():
    async def run():
        h, control = await charging()
        h.client.write_state.mode_change_at = h.clock[0]
        await control.async_set_lock_evse(True)
        assert control.pending_action == "lock"
        await control.async_set_limits(
            ControlOptions(
                read_only=False, current_cap=10, state_change_interval_seconds=300
            )
        )
        h.clock[0] += 60
        h.snapshot.values["charging_current_ems"] = 16.0
        before = currents(h)
        # The stop keeps its place and nothing is reported as lowered; the
        # stop ends the charge, and the cap bounds whatever starts after it.
        assert await control.async_enforce_current_cap() is False
        assert (control.pending_action, currents(h)) == ("lock", before)
        await control.async_shutdown()

    asyncio.run(run())


def test_a_foreign_current_above_the_maximum_is_not_saved_as_the_setpoint(tmp_path):
    async def run():
        service, _transport, _ = await service_fixture(tmp_path, current=40)
        await service.heartbeat.async_stop()
        await service.stop()
        restarted, _wallbox, _ = await service_fixture(tmp_path, current=40)
        await restarted.heartbeat.async_stop()
        rejected = restarted._intent_rejected
        await restarted.stop()
        assert not rejected

    asyncio.run(run())


def test_a_manual_release_does_not_pass_to_the_next_car_over_a_restart(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        control = service.control
        await control.async_configure_plug_charge(True, policy="a" * 64)
        _plug(transport, True)
        await service.refresh()
        clock[0] += 400
        await control.async_set_charging_release(True)
        assert (
            control.plug_charge["manual"]
        )
        assert (
            transport.bank[R.CHARGING_RELEASE.address] == 1
        )
        await service.save_intent()
        await service.stop()
        bank = dict(transport.bank)
        # car A leaves while the service is down
        restarted, wallbox, _ = await service_fixture(tmp_path)
        wallbox.bank.update(bank)
        _plug(wallbox, False, release=1)
        await restarted.heartbeat.async_stop()
        await restarted.refresh()
        await asyncio.sleep(0.05)
        # a different car plugs in
        _plug(wallbox, True)
        await restarted.refresh()
        await asyncio.sleep(0.05)
        rel = wallbox.bank[R.CHARGING_RELEASE.address]
        await restarted.stop()
        assert rel == 0

    asyncio.run(run())


def test_a_release_by_hand_for_the_next_plug_in_survives_a_restart():
    state = PlugChargeState()
    state.configure(True, "a" * 64)
    state.manual = True
    saved = state.durable()
    assert saved["manual_connected"] is False
    restored, cycle = PlugChargeState.restore(saved), PlugCycle()
    restored.follow(cycle, cycle.observe(False))
    # Given without a car: it still waits for the next one.
    assert restored.manual
    state.follow(cycle, cycle.observe(True))
    assert state.durable()["manual_connected"] is True
    restored, cycle = PlugChargeState.restore(state.durable()), PlugCycle()
    restored.follow(cycle, cycle.observe(False))
    assert not restored.manual
    with pytest.raises(ValueError, match="Plug & Charge"):
        PlugChargeState.restore({**saved, "manual": False, "manual_connected": True})


def test_a_release_given_after_a_restart_is_not_the_one_read_from_disk(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        gate = service.control._plug_charge
        await service.control.async_configure_plug_charge(True, policy="a" * 64)
        gate.state.restored_manual = True
        gate.set_manual(True)
        assert (gate.state.manual, gate.state.restored_manual) == (True, False)
        await service.stop()

    asyncio.run(run())
