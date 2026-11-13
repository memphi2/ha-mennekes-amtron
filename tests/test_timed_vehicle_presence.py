"""Departure commands do not outlive a freshly confirmed unplugging."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.data import WallboxData, WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device import control as device_control
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from tests.fakes import put, raise_connection_error
from tests.test_modbus_service import TOKEN, current_writes
from tests.test_timed_control import OWNER, Harness, future, service_fixture


@pytest.mark.parametrize("value", [None, 10.0])
@pytest.mark.parametrize("cp", [CpState.A1, CpState.A2])
def test_timed_write_cancels_only_after_live_consistent_absence(value, cp):
    async def run():
        harness = Harness(values={"evse_state": EvseState.CHARGING, "charging_current_ems": 1})
        control = await harness.connect()
        put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
        put(harness.transport.bank, R.CP_STATE, cp)
        await control.async_set_timed_current(value, deadline=future(), owner=OWNER)
        assert harness.written(R.CHARGING_CURRENT_EMS) == []
        assert control.events.snapshot()["events"][-1]["outcome"] == "cancelled"
        assert control.events.snapshot()["events"][-1]["reason"] == "unknown"
        assert control.write_state.current_write_generation == 0
        # A refused planner command did not consume the write floor. Explicit
        # manual commands retain their existing no-car bypass.
        await control.async_set_charging_current(10)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(("state", "cp"), [
    (EvseState.EV_CONNECTED, CpState.B1),
    (EvseState.CHARGING, CpState.A1),  # Conflicting readings are not absence.
    (EvseState.IDLE, CpState.B1),
    (EvseState.IDLE, CpState.INIT),
    (EvseState.IDLE, 99),
    (EvseState.ERROR, CpState.A1),
    (99, CpState.A1),
])
def test_unknown_or_conflicting_presence_is_not_cancelled_as_absent(state, cp):
    async def run():
        harness = Harness(values={"charging_current_ems": 1})
        control = await harness.connect()
        put(harness.transport.bank, R.EVSE_STATE, state)
        put(harness.transport.bank, R.CP_STATE, cp)
        await control.async_set_timed_current(10, deadline=future(), owner=OWNER)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert control.events.snapshot()["events"][-1]["outcome"] == "executed"
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("failed", [R.EVSE_STATE, R.CP_STATE])
def test_failed_presence_read_is_not_mistaken_for_proven_absence(failed):
    async def run():
        harness = Harness(values={"charging_current_ems": 1})
        control = await harness.connect()
        put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
        put(harness.transport.bank, R.CP_STATE, CpState.A1)
        harness.transport.read_exceptions[failed.address] = raise_connection_error()
        await control.async_set_timed_current(10, deadline=future(), owner=OWNER)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("delay", ["state_interval", "register_floor"])
def test_queued_timed_write_is_cancelled_when_vehicle_is_unplugged(delay):
    async def run():
        harness = Harness(
            state_change_interval_seconds=300 if delay == "state_interval" else 0,
            values={"evse_state": EvseState.EV_CONNECTED, "charging_current_ems": 1},
        )
        control = await harness.connect()
        control.pause_requested = True
        control.write_state.mode_change_at = harness.clock[0]
        if delay == "register_floor":
            control.write_state.limiter.record(R.CHARGING_CURRENT_EMS.key, now=harness.clock[0])
        sleeping, release = asyncio.Event(), asyncio.Event()

        async def sleep(_delay):
            sleeping.set()
            await release.wait()

        control._sleep = sleep
        try:
            await control.async_set_timed_current(10, deadline=future(), owner=OWNER)
            task = control._flush_task
            assert control.pending_action == "set_current"
            await asyncio.wait_for(sleeping.wait(), 2)
            put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
            put(harness.transport.bank, R.CP_STATE, CpState.A1)
            harness.clock[0] += 5
            release.set()
            await asyncio.wait_for(task, 2)
            assert control.pending_action is None
            assert control.pause_requested is True
            assert harness.written(R.CHARGING_CURRENT_EMS) == []
            assert control.events.snapshot()["events"][-1]["outcome"] == "cancelled"
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


def test_timed_presence_is_checked_after_waiting_for_bus():
    async def run():
        harness = Harness(values={"evse_state": EvseState.EV_CONNECTED, "charging_current_ems": 1})
        control = await harness.connect()
        async with harness.client._lock:
            writing = asyncio.create_task(
                control.async_set_timed_current(10, deadline=future(), owner=OWNER)
            )
            await asyncio.sleep(0)
            assert not writing.done()
            put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
            put(harness.transport.bank, R.CP_STATE, CpState.A1)
        await asyncio.wait_for(writing, 2)
        assert harness.written(R.CHARGING_CURRENT_EMS) == []
        assert control.events.snapshot()["events"][-1]["outcome"] == "cancelled"
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("changed", ["goal", "deadline", "connection"])
def test_timed_precondition_rechecks_goal_and_link_after_live_read(changed, monkeypatch):
    async def run():
        harness = Harness(values={"evse_state": EvseState.EV_CONNECTED, "charging_current_ems": 1})
        control = await harness.connect()
        reading, release = asyncio.Event(), asyncio.Event()
        original = harness.transport.read_holding_registers

        async def delayed_read(address, **kwargs):
            if address == R.EVSE_STATE.address:
                reading.set()
                await release.wait()
            return await original(address, **kwargs)

        harness.transport.read_holding_registers = delayed_read
        deadline = future()
        writing = asyncio.create_task(
            control.async_set_timed_current(10, deadline=deadline, owner=OWNER)
        )
        try:
            await asyncio.wait_for(reading.wait(), 2)
            if changed == "goal":
                control.soc_goal.observe(80)
            elif changed == "deadline":
                class ExpiredDatetime(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return deadline + timedelta(seconds=1)

                monkeypatch.setattr(device_control, "datetime", ExpiredDatetime)
            else:
                harness.client._connection_changed(False)
            release.set()
            if changed == "connection":
                with pytest.raises(HomeAssistantError):
                    await writing
            else:
                await writing
                reason = "energy_goal" if changed == "goal" else "deadline_expired"
                assert control.events.snapshot()["events"][-1]["reason"] == reason
            assert harness.written(R.CHARGING_CURRENT_EMS) == []
            assert control.write_state.current_write_generation == 0
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


def test_manual_handover_keeps_the_no_car_bypass():
    async def run():
        harness = Harness(
            state_change_interval_seconds=300,
            values={"evse_state": EvseState.EV_CONNECTED, "charging_current_ems": 1},
        )
        control = await harness.connect()
        control.pause_requested = True
        control.write_state.mode_change_at = harness.clock[0]
        sleeping, release = asyncio.Event(), asyncio.Event()

        async def sleep(_delay):
            sleeping.set()
            await release.wait()

        control._sleep = sleep
        try:
            await control.async_set_timed_current(10, deadline=future(), owner=OWNER)
            await asyncio.wait_for(sleeping.wait(), 2)
            await control.async_set_charging_current(12)
            assert control._pending.owner is None
            put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
            put(harness.transport.bank, R.CP_STATE, CpState.A1)
            task = control._flush_task
            release.set()
            await asyncio.wait_for(task, 2)
            assert current_writes(harness.transport) == [12]
            assert control.pause_requested is False
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("blocked", ["identity", "evse"])
def test_priority_heartbeat_passes_between_timed_presence_reads(blocked):
    async def run():
        harness = Harness(values={"charging_current_ems": 1})
        control = await harness.connect()
        harness.client.bind_identity("ABC123456789")
        put(harness.transport.bank, R.EVSE_STATE, EvseState.IDLE)
        put(harness.transport.bank, R.CP_STATE, CpState.B1)
        started, release = asyncio.Event(), asyncio.Event()
        trace = []
        original_read = harness.transport.read_holding_registers
        original_write = harness.transport.write_register

        async def read(address, **kwargs):
            name = {
                R.SERIAL_NUMBER.address: "identity",
                R.EVSE_STATE.address: "evse",
                R.CP_STATE.address: "cp",
            }.get(address)
            if name is not None:
                trace.append(name)
            if name == blocked:
                started.set()
                await release.wait()
            return await original_read(address, **kwargs)

        async def write(address, value, **kwargs):
            if address == R.HEARTBEAT.address:
                trace.append("heartbeat")
            return await original_write(address, value, **kwargs)

        harness.transport.read_holding_registers = read
        harness.transport.write_register = write
        timed = asyncio.create_task(
            control.async_set_timed_current(10, deadline=future(), owner=OWNER)
        )
        try:
            await asyncio.wait_for(started.wait(), 2)
            heartbeat = asyncio.create_task(
                harness.client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
            )
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(timed, heartbeat), 2)
            following = "evse" if blocked == "identity" else "cp"
            assert trace.index(blocked) < trace.index("heartbeat") < trace.index(following)
            assert current_writes(harness.transport) == [10]
        finally:
            release.set()
            await control.async_shutdown()

    asyncio.run(run())


def test_cancelled_timed_reacquire_cannot_release_the_heartbeat_lock():
    async def run():
        harness = Harness(values={"charging_current_ems": 1})
        control = await harness.connect()
        harness.client.bind_identity("ABC123456789")
        reading, finish_read = asyncio.Event(), asyncio.Event()
        writing, finish_write = asyncio.Event(), asyncio.Event()
        original_read = harness.transport.read_holding_registers
        original_write = harness.transport.write_register

        async def read(address, **kwargs):
            if address == R.SERIAL_NUMBER.address:
                reading.set()
                await finish_read.wait()
            return await original_read(address, **kwargs)

        async def write(address, value, **kwargs):
            if address == R.HEARTBEAT.address:
                writing.set()
                await finish_write.wait()
            return await original_write(address, value, **kwargs)

        harness.transport.read_holding_registers = read
        harness.transport.write_register = write
        timed = asyncio.create_task(
            control.async_set_timed_current(10, deadline=future(), owner=OWNER)
        )
        heartbeat = None
        try:
            await asyncio.wait_for(reading.wait(), 2)
            heartbeat = asyncio.create_task(
                harness.client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
            )
            await asyncio.sleep(0)
            finish_read.set()
            await asyncio.wait_for(writing.wait(), 2)
            timed.cancel()
            with pytest.raises(asyncio.CancelledError):
                await timed
            assert harness.client._lock.locked()
            assert not heartbeat.done()
            finish_write.set()
            await asyncio.wait_for(heartbeat, 2)
            assert not harness.client._lock.locked()
            assert current_writes(harness.transport) == []
        finally:
            finish_read.set()
            finish_write.set()
            if heartbeat is not None:
                await asyncio.gather(heartbeat, return_exceptions=True)
            await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("rollback", [False, True])
def test_gateway_configuration_during_timed_guard_prevents_stale_write(rollback):
    async def run():
        harness = Harness(values={"charging_current_ems": 1})
        control = await harness.connect()
        harness.client.bind_identity("ABC123456789")
        reading, finish_read = asyncio.Event(), asyncio.Event()
        configuring, finish_configuration = asyncio.Event(), asyncio.Event()
        original_read = harness.transport.read_holding_registers

        async def read(address, **kwargs):
            if address == R.SERIAL_NUMBER.address:
                reading.set()
                await finish_read.wait()
            return await original_read(address, **kwargs)

        async def configure():
            try:
                async with harness.client.async_connection_configuration() as guard:
                    configuring.set()
                    await finish_configuration.wait()
                    if rollback:
                        guard.allow_reconnect()
                        raise OSError("rolled back")
            except OSError:
                if not rollback:
                    raise

        harness.transport.read_holding_registers = read
        timed = asyncio.create_task(
            control.async_set_timed_current(10, deadline=future(), owner=OWNER)
        )
        configuration = None
        try:
            await asyncio.wait_for(reading.wait(), 2)
            configuration = asyncio.create_task(configure())
            await asyncio.sleep(0)
            finish_read.set()
            await asyncio.wait_for(configuring.wait(), 2)
            finish_configuration.set()
            await asyncio.wait_for(configuration, 2)
            with pytest.raises(HomeAssistantError):
                await asyncio.wait_for(timed, 2)
            # A confirmed rollback reopens the serial port; nothing else does.
            assert harness.client.connected is rollback
            assert current_writes(harness.transport) == []
            assert harness.client.write_state.current_write_generation == 0
        finally:
            finish_read.set()
            finish_configuration.set()
            if configuration is not None:
                await asyncio.gather(configuration, return_exceptions=True)
            await control.async_shutdown()

    asyncio.run(run())


def test_service_cancels_timed_resume_after_ha_disconnects(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        sleeping, release = asyncio.Event(), asyncio.Event()

        async def sleep(_delay):
            sleeping.set()
            await release.wait()

        try:
            await service.heartbeat.async_stop()
            put(transport.bank, R.EVSE_STATE, EvseState.EV_CONNECTED)
            put(transport.bank, R.CP_STATE, CpState.B1)
            await service.refresh()
            service.control._sleep = sleep
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                    state=lambda: WallboxData(), request_refresh=None,
                )
                await control.async_set_timed_current(None, deadline=future(), owner=OWNER)
                await control.async_set_timed_current(10, deadline=future(), owner=OWNER)
                task = service.control._flush_task
                assert service.control.pending_action == "set_current"
                await asyncio.wait_for(sleeping.wait(), 2)
                await remote.async_close()  # No HA observer remains to cancel the queue.
                put(transport.bank, R.EVSE_STATE, EvseState.IDLE)
                put(transport.bank, R.CP_STATE, CpState.A1)
                clock[0] += 5
                release.set()
                await asyncio.wait_for(task, 2)
                assert service.control.pending_action is None
                assert service.control.pause_requested is True
                assert current_writes(transport) == [1]
                assert service.control.events.snapshot()["events"][-1]["outcome"] == "cancelled"
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())
