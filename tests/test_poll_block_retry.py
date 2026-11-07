"""Retry failed blocks without repeatedly reading the rest of the bus."""

from __future__ import annotations

import asyncio

import pytest
from pymodbus.exceptions import ModbusIOException

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
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
from custom_components.mennekes_amtron_rtu.device.runtime_events import EventTimeline
from tests.fakes import put, raise_connection_error
from tests.test_control import Harness
from tests.test_energy_goal_controller import _runner, _settle
from tests.test_modbus_service import current_writes


@pytest.mark.parametrize("failure", ["timeout", "device_exception", "mixed"])
def test_all_failed_reads_distinguish_missing_replies_from_device_exceptions(failure):
    async def run():
        h = Harness()
        await h.connect()
        state = ConnectionState()
        timeline = EventTimeline()
        poller = DevicePoller(h.client, h.identity, connection_state=state, timeline=timeline)
        data = await poller.async_poll(WallboxData(), last_success=True)
        failure_response = 4 if failure == "device_exception" else ModbusIOException("No response received")
        h.transport.read_exceptions.update({block.address: failure_response for block in REGISTER_BLOCKS})
        if failure == "mixed":
            h.transport.read_exceptions[R.EVSE_STATE.address] = 4

        for _ in range(2):
            with pytest.raises(AmtronConnectionError, match="no register block"):
                await poller.async_poll(data, last_success=False)
        assert state.available is (failure != "timeout")
        assert [row["event"] for row in timeline.snapshot()] == (
            ["connection_lost"] if failure == "timeout" else []
        )

        h.transport.read_exceptions.clear()
        await poller.async_poll(data, last_success=False)
        assert state.available
        assert [row["event"] for row in timeline.snapshot()] == (
            ["connection_lost", "connection_recovered"] if failure == "timeout" else []
        )

    asyncio.run(run())


@pytest.mark.parametrize("state", [EvseState.IDLE, EvseState.CHARGING])
@pytest.mark.parametrize(("name", "address"), [("statistics", 0x1000), ("measurements", 0x0500)])
def test_transient_block_failure_retries_only_that_block(state, name, address):
    async def run():
        h = Harness(values={"evse_state": state, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(
            h.client, h.identity, connection_state=ConnectionState(),
            measurement_interval=30, monotonic=lambda: h.clock[0],
        )
        h.transport.read_exceptions[address] = 0x04
        data = await poller.async_poll(WallboxData(), last_success=True)
        assert data.failed_blocks == (name,)
        h.transport.read_exceptions.clear()

        h.clock[0] += 4
        h.transport.reads.clear()
        data = await poller.async_poll(data, last_success=True)
        assert data.failed_blocks == (name,)
        assert address not in {a for a, _ in h.transport.reads}

        h.clock[0] += 1
        h.transport.reads.clear()
        data = await poller.async_poll(data, last_success=True)
        reads = {a for a, _ in h.transport.reads}
        fast = {block.address for block in REGISTER_BLOCKS if block.cadence is BlockCadence.FAST}
        assert reads == fast | {address}
        assert data.failed_blocks == ()
        assert data.energy_read is (name == "statistics")

        # Recovery must not advance the cadence of the other active blocks.
        h.clock[0] += 1
        h.transport.reads.clear()
        await poller.async_poll(data, last_success=True)
        assert {a for a, _ in h.transport.reads} == fast

    asyncio.run(run())


def test_persistent_slow_block_failure_backs_off_without_repolling_other_blocks():
    async def run():
        h = Harness(values={"evse_state": EvseState.CHARGING, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(h.client, h.identity, connection_state=ConnectionState(), monotonic=lambda: h.clock[0])
        data = await poller.async_poll(WallboxData(), last_success=True)
        start = h.clock[0]
        h.transport.read_exceptions[0x1000] = 0x04
        slow = {block.address for block in REGISTER_BLOCKS if block.cadence is BlockCadence.SLOW}

        # The normal 60-second refresh can preempt a pending retry; only it
        # reads all slow blocks. Repeated failures back off to that interval.
        for offset, statistics_read, full_slow in (
            (60, True, True), (65, True, False), (70, False, False),
            (75, True, False), (85, False, False), (95, True, False),
            (115, False, False), (120, True, True), (125, False, False),
            (175, False, False), (180, True, True),
        ):
            h.clock[0] = start + offset
            h.transport.reads.clear()
            data = await poller.async_poll(data, last_success=True)
            reads = {a for a, _ in h.transport.reads}
            assert (0x1000 in reads) is statistics_read
            assert (reads & (slow - {0x1000})) == (slow - {0x1000} if full_slow else set())
            assert data.failed_blocks == ("statistics",)

    asyncio.run(run())


@pytest.mark.parametrize("state", [EvseState.IDLE, EvseState.CHARGING])
def test_persistent_active_block_backs_off_without_repolling_other_measurements(state):
    async def run():
        h = Harness(values={"evse_state": state, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(
            h.client, h.identity, connection_state=ConnectionState(),
            measurement_interval=300, monotonic=lambda: h.clock[0],
        )
        h.transport.read_exceptions[0x0500] = 0x04
        data = await poller.async_poll(WallboxData(), last_success=True)
        start = h.clock[0]
        other_active = {
            block.address for block in REGISTER_BLOCKS
            if block.cadence is BlockCadence.ACTIVE and block.address != 0x0500
        }
        for offset, measurement_read in (
            (5, True), (10, False), (15, True), (25, False), (35, True),
            (60, False), (74, False), (75, True), (100, False),
            (135, True), (194, False), (195, True),
        ):
            h.clock[0] = start + offset
            h.transport.reads.clear()
            data = await poller.async_poll(data, last_success=True)
            reads = {a for a, _ in h.transport.reads}
            assert (0x0500 in reads) is measurement_read
            assert not reads & other_active
            assert data.failed_blocks == ("measurements",)

    asyncio.run(run())


def test_a_recovered_block_gets_a_fresh_short_retry_after_a_later_failure():
    async def run():
        h = Harness(values={"evse_state": EvseState.CHARGING, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(h.client, h.identity, connection_state=ConnectionState(), monotonic=lambda: h.clock[0])
        h.transport.read_exceptions[0x1000] = 0x04
        data = await poller.async_poll(WallboxData(), last_success=True)
        start = h.clock[0]
        h.clock[0] = start + 5
        data = await poller.async_poll(data, last_success=True)
        h.transport.read_exceptions.clear()
        h.clock[0] = start + 15
        data = await poller.async_poll(data, last_success=True)
        assert data.failed_blocks == ()

        h.transport.read_exceptions[0x1000] = 0x04
        h.clock[0] = start + 60
        data = await poller.async_poll(data, last_success=True)
        h.transport.read_exceptions.clear()
        h.clock[0] = start + 65
        h.transport.reads.clear()
        data = await poller.async_poll(data, last_success=True)
        assert 0x1000 in {address for address, _ in h.transport.reads}
        assert data.failed_blocks == ()

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [float("nan"), -1.0])
def test_invalid_meter_value_is_retried_without_repolling_other_slow_blocks(invalid):
    async def run():
        h = Harness(values={"evse_state": EvseState.CHARGING, "charging_current_ems": 16.0})
        await h.connect()
        poller = DevicePoller(h.client, h.identity, connection_state=ConnectionState(), monotonic=lambda: h.clock[0])
        put(h.transport.bank, R.ENERGY_TOTAL, invalid)
        data = await poller.async_poll(WallboxData(), last_success=True)
        assert data.energy_read
        assert data.get("energy_total") is None or data.get("energy_total") < 0
        put(h.transport.bank, R.ENERGY_TOTAL, 1234.5)
        h.clock[0] += 5
        h.transport.reads.clear()
        data = await poller.async_poll(data, last_success=True)
        assert data.energy_read
        assert data.get("energy_total") == pytest.approx(1234.5)
        slow = {block.address for block in REGISTER_BLOCKS if block.cadence is BlockCadence.SLOW}
        assert {a for a, _ in h.transport.reads} & slow == {0x1000}

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["block", "disconnect"])
def test_energy_goal_receives_fresh_meter_after_transient_failure(failure):
    async def run():
        h, control, goal, _saved = await _runner()
        poller = DevicePoller(h.client, h.identity, connection_state=ConnectionState(), monotonic=lambda: h.clock[0])
        await goal.async_set_target(12)
        h.snapshot = await poller.async_poll(WallboxData(), last_success=True)
        goal.observe(h.snapshot)
        await _settle(goal)

        h.clock[0] += 60 if failure == "block" else 5
        address = 0x1000 if failure == "block" else R.EVSE_STATE.address
        h.transport.read_exceptions[address] = 0x04 if failure == "block" else raise_connection_error()
        if failure == "disconnect":
            with pytest.raises(AmtronConnectionError):
                await poller.async_poll(h.snapshot, last_success=True)
            # This is the failure observation emitted by the HA and service owners.
            goal.observe(None)
        else:
            h.snapshot = await poller.async_poll(h.snapshot, last_success=True)
            goal.observe(h.snapshot)
        await _settle(goal)
        assert control.energy_goal.status == "meter_unavailable"
        assert current_writes(h.transport) == [1]

        h.transport.read_exceptions.clear()
        h.clock[0] += 5
        h.transport.reads.clear()
        h.snapshot = await poller.async_poll(h.snapshot, last_success=failure != "disconnect")
        assert h.snapshot.energy_read
        assert 0x1000 in {a for a, _ in h.transport.reads}
        goal.observe(h.snapshot)
        await _settle(goal)
        assert control.energy_goal.status == "active"
        await goal.async_stop()
        await control.async_shutdown()

    asyncio.run(run())
