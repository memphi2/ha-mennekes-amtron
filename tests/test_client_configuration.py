"""An explicit gateway update excludes traffic until restart or verified rollback."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.registers import (
    CHARGING_CURRENT_EMS,
    EVSE_STATE,
    HEARTBEAT,
)
from tests.fakes import FakeModbusClient, device_bank


def make_client(transport):
    return MennekesModbusClient(
        NetworkConfig("gateway.local"), client_factory=lambda _: transport,
    )


def test_successful_gateway_update_blocks_queued_reads_writes_and_reconnect():
    async def run():
        transport = FakeModbusClient(device_bank())
        client = make_client(transport)
        await client.async_connect()
        async with client.async_connection_configuration():
            assert not client.connected
            assert not transport.connected
            reads = asyncio.create_task(client.async_read_register(EVSE_STATE))
            writes = asyncio.create_task(client.async_write_register(HEARTBEAT, 0x55AA, priority=True))
            await asyncio.sleep(0)
            assert not reads.done()
            assert not writes.done()
        for task in (reads, writes):
            with pytest.raises(AmtronConnectionError, match="restart"):
                await task
        with pytest.raises(AmtronConnectionError, match="restart"):
            await client.async_connect()
        with pytest.raises(AmtronConnectionError, match="restart"):
            await client.async_read_words(address=EVSE_STATE.address, count=1)
        assert transport.reads == transport.writes == []
        assert not client.connected
        await client.async_close()
        replacement = make_client(FakeModbusClient(device_bank()))
        await replacement.async_connect()
        assert await replacement.async_read_register(EVSE_STATE) == 1
        await replacement.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_failure_or_cancellation_resumes_only_after_explicit_rollback_confirmation(verified, cancelled):
    async def run():
        transport = FakeModbusClient(device_bank())
        client = make_client(transport)
        await client.async_connect()
        exception = asyncio.CancelledError if cancelled else OSError
        async def fail():
            async with client.async_connection_configuration() as guard:
                if verified:
                    guard.allow_reconnect()
                raise exception("simulated configuration failure")

        with pytest.raises(exception):
            await fail()
        if verified:
            await client.async_connect()
            assert await client.async_read_register(EVSE_STATE) == 1
        else:
            with pytest.raises(AmtronConnectionError, match="restart"):
                await client.async_connect()
        await client.async_close()

    asyncio.run(run())


def test_unnecessary_rollback_marker_never_resumes_after_successful_apply():
    async def run():
        client = make_client(FakeModbusClient(device_bank()))
        await client.async_connect()
        async with client.async_connection_configuration() as guard:
            guard.allow_reconnect()
        with pytest.raises(AmtronConnectionError, match="restart"):
            await client.async_connect()
        # A later failed settings edit cannot remove a previous restart requirement.
        async def fail_again():
            async with client.async_connection_configuration() as guard:
                guard.allow_reconnect()
                raise OSError("second attempt")

        with pytest.raises(OSError, match="second attempt"):
            await fail_again()
        with pytest.raises(AmtronConnectionError, match="restart"):
            await client.async_connect()
        await client.async_close()

    asyncio.run(run())


def test_configuration_waits_for_inflight_bus_transaction_before_closing():
    async def run():
        transport = FakeModbusClient(device_bank())
        started = asyncio.Event()
        release = asyncio.Event()
        original_read = transport.read_holding_registers

        async def read(*args, **kwargs):
            started.set()
            await release.wait()
            return await original_read(*args, **kwargs)

        transport.read_holding_registers = read
        client = make_client(transport)
        await client.async_connect()
        read_task = asyncio.create_task(client.async_read_register(EVSE_STATE))
        await started.wait()
        entered = asyncio.Event()

        async def configure():
            async with client.async_connection_configuration():
                entered.set()

        config_task = asyncio.create_task(configure())
        await asyncio.sleep(0)
        assert not entered.is_set()
        assert transport.close_calls == 0
        release.set()
        assert await read_task == 1
        await config_task
        assert entered.is_set()
        assert transport.close_calls == 1
        await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 3))


def test_blocked_current_write_does_not_consume_write_floor_or_generation():
    async def run():
        client = make_client(FakeModbusClient(device_bank()))
        await client.async_connect()
        async with client.async_connection_configuration():
            pass
        with pytest.raises(AmtronConnectionError, match="restart"):
            await client.async_write_register(CHARGING_CURRENT_EMS, 8)
        assert client.write_state.current_write_generation == 0
        client.check_write_interval(CHARGING_CURRENT_EMS)
        assert client.write_trace.snapshot() == []
        await client.async_close()

    asyncio.run(run())
