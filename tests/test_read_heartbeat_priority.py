"""Identity and data reads yield to the heartbeat without trusting a new link."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.register_blocks import REGISTER_BLOCKS
from tests.fakes import FakeModbusClient, device_bank


@pytest.mark.parametrize("operation", ["register", "raw", "block"])
@pytest.mark.parametrize("change", ["none", "generation", "disconnected", "identity", "configuration", "transport"])
def test_heartbeat_can_pass_after_identity_without_reusing_stale_evidence(operation, change):
    async def run():
        transport = FakeModbusClient(device_bank())
        replacement = FakeModbusClient(device_bank())
        replacement.connected = True
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        client.bind_identity("ABC123456789")
        started, release = asyncio.Event(), asyncio.Event()
        events = []
        original_read = transport.read_holding_registers
        original_write = transport.write_register

        async def read(address, *, count=1, device_id=1):
            if address == R.SERIAL_NUMBER.address:
                started.set()
                await release.wait()
                result = await original_read(address, count=count, device_id=device_id)
                events.append("identity")
                return result
            events.append("data")
            return await original_read(address, count=count, device_id=device_id)

        async def write(address, value, *, device_id=1):
            result = await original_write(address, value, device_id=device_id)
            events.append("heartbeat")
            if change == "generation":
                client._connection_changed(True)
            elif change == "disconnected":
                transport.connected = False
            elif change == "identity":
                client.bind_identity("ABC123456789")
            elif change == "configuration":
                client._configuration_pending_restart = True
            elif change == "transport":
                client._client = replacement
            return result

        transport.read_holding_registers = read
        transport.write_register = write
        if operation == "register":
            read_coro = client.async_read_register(R.EVSE_STATE)
        elif operation == "raw":
            read_coro = client.async_read_words(address=R.EVSE_STATE.address, count=R.EVSE_STATE.count)
        else:
            block = next(block for block in REGISTER_BLOCKS if block.address == R.EVSE_STATE.address)
            read_coro = client.async_read_block(block)
        read_task = asyncio.create_task(read_coro)
        beat = None
        try:
            await asyncio.wait_for(started.wait(), 1)
            beat = asyncio.create_task(client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True))
            await asyncio.sleep(0)
            assert not beat.done()
            release.set()
            await asyncio.wait_for(beat, 1)
            if change == "none":
                await asyncio.wait_for(read_task, 1)
                assert events == ["identity", "heartbeat", "data"]
            else:
                with pytest.raises(AmtronConnectionError):
                    await asyncio.wait_for(read_task, 1)
                assert events == ["identity", "heartbeat"]
        finally:
            release.set()
            await asyncio.gather(read_task, *([beat] if beat is not None else []), return_exceptions=True)
            client._configuration_pending_restart = False
            await client.async_close()
            transport.close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_cancelling_a_read_waiting_behind_heartbeat_releases_both_locks():
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        client.bind_identity("ABC123456789")
        identity_started, identity_release = asyncio.Event(), asyncio.Event()
        beat_started, beat_release = asyncio.Event(), asyncio.Event()
        original_read = transport.read_holding_registers
        original_write = transport.write_register

        async def read(address, *, count=1, device_id=1):
            if address == R.SERIAL_NUMBER.address:
                identity_started.set()
                await identity_release.wait()
            return await original_read(address, count=count, device_id=device_id)

        async def write(address, value, *, device_id=1):
            beat_started.set()
            await beat_release.wait()
            return await original_write(address, value, device_id=device_id)

        transport.read_holding_registers = read
        transport.write_register = write
        pending = asyncio.create_task(client.async_read_register(R.EVSE_STATE))
        beat = None
        try:
            await asyncio.wait_for(identity_started.wait(), 1)
            beat = asyncio.create_task(client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True))
            await asyncio.sleep(0)
            identity_release.set()
            await asyncio.wait_for(beat_started.wait(), 1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            beat_release.set()
            await asyncio.wait_for(beat, 1)
            assert await asyncio.wait_for(client.async_read_register(R.EVSE_STATE), 1) == 1
        finally:
            identity_release.set()
            beat_release.set()
            await asyncio.gather(pending, *([beat] if beat is not None else []), return_exceptions=True)
            await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))
