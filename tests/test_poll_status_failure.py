"""A missing EVSE read must not manufacture a plug/start/stop transition."""

from __future__ import annotations

import asyncio

import pytest
from pymodbus.exceptions import ModbusIOException

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import (
    ConnectionState,
    WallboxData,
)
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from custom_components.mennekes_amtron_rtu.device.poller import DevicePoller
from custom_components.mennekes_amtron_rtu.device.register_blocks import (
    REGISTER_BLOCKS,
    BlockCadence,
)
from tests.fakes import put
from tests.test_control import Harness

FAST = {block.address for block in REGISTER_BLOCKS if block.cadence is BlockCadence.FAST}
ACTIVE = {block.address for block in REGISTER_BLOCKS if block.cadence is BlockCadence.ACTIVE}
STATISTICS = next(block.address for block in REGISTER_BLOCKS if block.name == "statistics")


@pytest.mark.parametrize("state", [EvseState.IDLE, EvseState.CHARGING])
@pytest.mark.parametrize("failure", ["timeout", "device_exception"])
def test_failed_status_does_not_accelerate_other_blocks(state, failure):
    async def run():
        h = Harness(values={"evse_state": state, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(
            h.client, h.identity, connection_state=ConnectionState(),
            measurement_interval=30, monotonic=lambda: h.clock[0],
        )
        data = await poller.async_poll(WallboxData(), last_success=True)
        start = h.clock[0]
        h.transport.read_exceptions[R.EVSE_STATE.address] = (
            ModbusIOException("No response received") if failure == "timeout" else 4
        )
        for offset in (5, 10, 15):
            h.clock[0] = start + offset
            h.transport.reads.clear()
            data = await poller.async_poll(data, last_success=True)
            assert {address for address, _count in h.transport.reads} == FAST
            assert "status" in data.failed_blocks
            assert data.get("evse_state") == state
            assert data.fresh("evse_state") is None
            assert not data.energy_read

        # An unchanged status recovering inside the hold interval is not a
        # transition either. The ordinary measurement deadline still applies.
        h.transport.read_exceptions.clear()
        h.clock[0] = start + 20
        h.transport.reads.clear()
        data = await poller.async_poll(data, last_success=True)
        assert {address for address, _count in h.transport.reads} == FAST
        assert data.failed_blocks == ()
        h.clock[0] = start + (60 if state == EvseState.IDLE else 30)
        h.transport.reads.clear()
        await poller.async_poll(data, last_success=True)
        assert {address for address, _count in h.transport.reads} >= ACTIVE

    asyncio.run(run())


@pytest.mark.parametrize(("before", "after"), [
    (EvseState.IDLE, EvseState.CHARGING),
    (EvseState.CHARGING, EvseState.IDLE),
])
def test_recovery_with_a_real_transition_refreshes_measurements_and_energy(before, after):
    async def run():
        h = Harness(values={"evse_state": before, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(
            h.client, h.identity, connection_state=ConnectionState(),
            measurement_interval=30, monotonic=lambda: h.clock[0],
        )
        data = await poller.async_poll(WallboxData(), last_success=True)
        h.clock[0] += 5
        h.transport.read_exceptions[R.EVSE_STATE.address] = 4
        data = await poller.async_poll(data, last_success=True)
        h.transport.read_exceptions.clear()
        put(h.transport.bank, R.EVSE_STATE, after)
        h.clock[0] += 5
        h.transport.reads.clear()
        data = await poller.async_poll(data, last_success=True)
        assert {address for address, _count in h.transport.reads} == FAST | ACTIVE | {STATISTICS}
        assert data.get("evse_state") == after
        assert data.energy_read

    asyncio.run(run())
