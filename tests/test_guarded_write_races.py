"""Guarded operations recheck the wallbox at the actual write boundary."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    HEARTBEAT_VALUE,
    MIN_PHASE_CHANGE_INTERVAL_SECONDS,
)
from custom_components.mennekes_amtron_rtu.device.enums import (
    CpState,
    EvseState,
    RequestedPhases,
)
from tests.test_control import Harness


@pytest.mark.parametrize("operation", ["restart", "phase_idle", "phase_paused"])
def test_guarded_write_rechecks_state_after_another_bus_user(operation: str) -> None:
    """A queued read may intervene after the initial status confirmation."""

    async def run() -> None:
        paused = operation == "phase_paused"
        state = EvseState.EV_CONNECTED if paused else EvseState.IDLE
        harness = Harness(values={"evse_state": state}, phase_switch_grace_seconds=0)
        harness.transport.bank[R.CP_STATE.address] = int(CpState.B1 if paused else CpState.A1)
        control = await harness.connect()
        guard_address = R.EVSE_STATE.address if operation == "restart" else R.CP_STATE.address
        read = harness.transport.read_holding_registers
        reader_started = asyncio.Event()
        finish_reader = asyncio.Event()
        extra_reader: asyncio.Task[object] | None = None

        async def read_with_intervening_request(address: int, *, count: int, device_id: int):
            nonlocal extra_reader
            if address == R.ERROR_CODE.address:
                reader_started.set()
                await finish_reader.wait()
            result = await read(address, count=count, device_id=device_id)
            if address == guard_address and extra_reader is None:
                # Queue an ordinary read before the initial guard releases its
                # queue slot. It occupies the bus while the wallbox changes.
                extra_reader = asyncio.create_task(control._client.async_read_register(R.ERROR_CODE))
                await asyncio.sleep(0)
            return result

        harness.transport.read_holding_registers = read_with_intervening_request
        command = asyncio.create_task(
            control.async_restart() if operation == "restart"
            else control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        )
        await asyncio.wait_for(reader_started.wait(), 1)
        harness.transport.bank[R.EVSE_STATE.address] = int(EvseState.CHARGING)
        harness.transport.bank[R.CP_STATE.address] = int(CpState.C2)
        finish_reader.set()
        with pytest.raises(ServiceValidationError) as error:
            await asyncio.wait_for(command, 1)
        assert error.value.translation_key == (
            "restart_requires_idle" if operation == "restart" else "phase_pause_state_unknown"
        )
        assert extra_reader is not None
        await extra_reader
        assert not harness.written(R.SYSTEM_RESTART)
        assert not harness.written(R.REQUESTED_PHASES)
        harness.client.check_write_interval(
            R.REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS,
        )
        if paused:
            assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        else:
            assert not harness.written(R.CHARGING_CURRENT_EMS)

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["restart", "phase"])
def test_guarded_write_rejects_reconnect_during_final_status_read(operation: str) -> None:
    async def run() -> None:
        harness = Harness(values={"evse_state": EvseState.IDLE})
        harness.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        control = await harness.connect()
        read = harness.transport.read_holding_registers
        state_reads = 0
        final_read = 2 if operation == "restart" else 3

        async def reconnect_during_read(address: int, *, count: int, device_id: int):
            nonlocal state_reads
            result = await read(address, count=count, device_id=device_id)
            if address == R.EVSE_STATE.address:
                state_reads += 1
                if state_reads == final_read:
                    # Model pymodbus' connection callbacks around a reconnect,
                    # including a transport already open again by the time the
                    # guard observes it.
                    harness.transport.close()
                    harness.client._trace_connect(False)
                    await harness.transport.connect()
                    harness.client._trace_connect(True)
            return result

        harness.transport.read_holding_registers = reconnect_during_read
        command = (
            control.async_restart() if operation == "restart"
            else control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        )
        with pytest.raises(HomeAssistantError):
            await command
        assert state_reads == final_read
        assert not harness.written(R.SYSTEM_RESTART)
        assert not harness.written(R.REQUESTED_PHASES)
        harness.client.check_write_interval(
            R.REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS,
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    ("operation", "address", "last_read"),
    [
        ("restart", R.EVSE_STATE.address, 2),
        ("phase", R.EVSE_STATE.address, 3),
        ("phase", R.CP_STATE.address, 2),
    ],
)
@pytest.mark.parametrize("failure", ["protocol", "invalid"])
def test_guarded_write_rejects_failed_or_invalid_final_status(
    operation: str, address: int, last_read: int, failure: str,
) -> None:
    async def run() -> None:
        harness = Harness(values={"evse_state": EvseState.IDLE})
        harness.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        control = await harness.connect()
        read = harness.transport.read_holding_registers
        attempts = 0

        async def fail_final_read(register: int, *, count: int, device_id: int):
            nonlocal attempts
            if register == address:
                attempts += 1
                if attempts == last_read:
                    if failure == "protocol":
                        harness.transport.read_exceptions[address] = 4
                    else:
                        harness.transport.bank[address] = 99
            return await read(register, count=count, device_id=device_id)

        harness.transport.read_holding_registers = fail_final_read
        command = (
            control.async_restart() if operation == "restart"
            else control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        )
        with pytest.raises(ServiceValidationError) as error:
            await command
        assert error.value.translation_key == (
            "restart_state_unknown" if operation == "restart" else "phase_pause_state_unknown"
        )
        assert attempts == last_read
        assert not harness.written(R.SYSTEM_RESTART)
        assert not harness.written(R.REQUESTED_PHASES)
        harness.client.check_write_interval(
            R.REQUESTED_PHASES, min_interval=MIN_PHASE_CHANGE_INTERVAL_SECONDS,
        )

    asyncio.run(run())


def test_final_phase_guard_supports_older_layout_without_cp_register() -> None:
    async def run() -> None:
        harness = Harness(values={"evse_state": EvseState.IDLE})
        harness.identity.layout_version = R.LAYOUT_V01_01
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert all(address != R.CP_STATE.address for address, _ in harness.transport.reads)

    asyncio.run(run())


def test_phase_write_cancels_if_a_vehicle_reconnects_after_confirmed_unplug() -> None:
    async def run() -> None:
        harness = Harness(
            values={"evse_state": EvseState.EV_CONNECTED}, phase_switch_grace_seconds=5,
        )
        harness.transport.bank[R.CP_STATE.address] = int(CpState.B1)
        control = await harness.connect()
        read = harness.transport.read_holding_registers
        state_reads = 0
        reader_started = asyncio.Event()
        finish_reader = asyncio.Event()
        extra_reader: asyncio.Task[object] | None = None

        async def read_with_replug(address: int, *, count: int, device_id: int):
            nonlocal state_reads, extra_reader
            if address == R.ERROR_CODE.address:
                reader_started.set()
                await finish_reader.wait()
            if address == R.EVSE_STATE.address:
                state_reads += 1
                if state_reads == 2:
                    # Unplug just after the phase operation's pause write.
                    harness.transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
                    harness.transport.bank[R.CP_STATE.address] = int(CpState.A1)
            result = await read(address, count=count, device_id=device_id)
            if address == R.CP_STATE.address and extra_reader is None:
                extra_reader = asyncio.create_task(harness.client.async_read_register(R.ERROR_CODE))
                await asyncio.sleep(0)
            return result

        harness.transport.read_holding_registers = read_with_replug
        command = asyncio.create_task(
            control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        )
        await asyncio.wait_for(reader_started.wait(), 1)
        harness.transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        harness.transport.bank[R.CP_STATE.address] = int(CpState.B1)
        finish_reader.set()
        with pytest.raises(ServiceValidationError) as error:
            await asyncio.wait_for(command, 1)
        assert error.value.translation_key == "phase_pause_state_unknown"
        assert extra_reader is not None
        await extra_reader
        assert harness.clock[0] == 1000
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert not harness.written(R.REQUESTED_PHASES)

    asyncio.run(run())


def test_heartbeat_passes_between_final_phase_guard_reads() -> None:
    async def run() -> None:
        harness = Harness(values={"evse_state": EvseState.IDLE})
        harness.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        control = await harness.connect()
        read = harness.transport.read_holding_registers
        write = harness.transport.write_register
        state_reads = 0
        events: list[str] = []
        beat: asyncio.Task[None] | None = None

        async def read_with_heartbeat(address: int, *, count: int, device_id: int):
            nonlocal state_reads, beat
            result = await read(address, count=count, device_id=device_id)
            if address == R.EVSE_STATE.address:
                state_reads += 1
                if state_reads == 3:
                    beat = asyncio.create_task(harness.client.async_write_register(
                        R.HEARTBEAT, HEARTBEAT_VALUE, priority=True,
                    ))
                    await asyncio.sleep(0)
            elif address == R.CP_STATE.address and state_reads == 3:
                events.append("cp_read")
            return result

        async def note_heartbeat(address: int, value: int, *, device_id: int):
            if address == R.HEARTBEAT.address:
                events.append("heartbeat")
            return await write(address, value, device_id=device_id)

        harness.transport.read_holding_registers = read_with_heartbeat
        harness.transport.write_register = note_heartbeat
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert beat is not None
        await beat
        assert events == ["heartbeat", "cp_read"]
        assert harness.written(R.REQUESTED_PHASES) == [[1]]

    asyncio.run(run())
