"""Fresh goal-stop confirmation leaves the heartbeat its bus priority."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.heartbeat import HeartbeatTask
from tests.fakes import put
from tests.test_energy_goal_controller import _runner
from tests.test_modbus_service import current_writes


def test_confirming_a_paused_goal_reads_current_once_and_releases_the_bus():
    async def run():
        harness, control, goal, _saved = await _runner(paused=True)
        client = harness.client
        client.bind_identity("ABC123456789")
        await goal.async_set_target(0.1)
        assert client.identity_verified
        control.energy_goal.observe(1234.8)
        harness.transport.reads.clear()
        started, release = asyncio.Event(), asyncio.Event()
        read = harness.transport.read_holding_registers

        async def delayed(address, *, count=1, device_id=1):
            if address == R.CHARGING_CURRENT_EMS.address:
                started.set()
                await release.wait()
            return await read(address, count=count, device_id=device_id)

        harness.transport.read_holding_registers = delayed
        heartbeat = HeartbeatTask(client, harness.diagnostics)
        pause = asyncio.create_task(control.async_pause_for_energy_goal())
        beat = None
        try:
            await asyncio.wait_for(started.wait(), 1)
            beat = asyncio.create_task(heartbeat.async_beat_once())
            await asyncio.sleep(0)
            assert not beat.done()
            release.set()
            await asyncio.wait_for(pause, 1)
            assert await asyncio.wait_for(beat, 1)
            # A plugged-in car is confirmed first; it needs no plug reading.
            assert harness.transport.reads == [
                (R.EVSE_STATE.address, R.EVSE_STATE.count),
                (R.CHARGING_CURRENT_EMS.address, R.CHARGING_CURRENT_EMS.count),
            ]
            assert current_writes(harness.transport) == []
        finally:
            release.set()
            await asyncio.gather(pause, *([beat] if beat is not None else []), return_exceptions=True)
            await goal.async_stop()
            await control.async_shutdown()
            await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_goal_stop_still_checks_identity_before_a_needed_write():
    async def run():
        harness, control, goal, _saved = await _runner(paused=True)
        client = harness.client
        client.bind_identity("ABC123456789")
        await goal.async_set_target(0.1)
        assert client.identity_verified
        control.energy_goal.observe(1234.8)
        put(harness.transport.bank, R.SERIAL_NUMBER, "DIFFERENTBOX")
        put(harness.transport.bank, R.CHARGING_CURRENT_EMS, 16)
        harness.transport.reads.clear()
        try:
            with pytest.raises(DeviceError, match="device_identity_changed"):
                await control.async_pause_for_energy_goal()
            assert harness.transport.reads == [
                (R.EVSE_STATE.address, R.EVSE_STATE.count),
                (R.CHARGING_CURRENT_EMS.address, R.CHARGING_CURRENT_EMS.count),
                (R.SERIAL_NUMBER.address, R.SERIAL_NUMBER.count),
            ]
            assert not client.identity_verified
            assert current_writes(harness.transport) == []
        finally:
            await goal.async_stop()
            await control.async_shutdown()
            await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_a_reached_goal_pauses_only_once_a_car_is_plugged_in():
    async def run():
        harness, control, goal, _saved = await _runner(paused=False)
        client = harness.client
        client.bind_identity("ABC123456789")
        await goal.async_set_target(0.1)
        control.energy_goal.observe(1234.8)
        put(harness.transport.bank, R.EVSE_STATE, int(EvseState.IDLE))
        put(harness.transport.bank, R.CP_STATE, int(CpState.A1))
        put(harness.transport.bank, R.CHARGING_CURRENT_EMS, 16)
        try:
            # Without a car, the wallbox has nothing to pause.
            await control.async_pause_for_energy_goal()
            assert current_writes(harness.transport) == []
            assert control.pause_requested is not True
            put(harness.transport.bank, R.EVSE_STATE, int(EvseState.EV_CONNECTED))
            put(harness.transport.bank, R.CP_STATE, int(CpState.B1))
            await control.async_pause_for_energy_goal()
            assert current_writes(harness.transport)
        finally:
            await goal.async_stop()
            await control.async_shutdown()
            await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))
