"""Physical device identity, rather than cached metadata, authorizes writes."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import (
    NetworkConfig,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.identity import async_read_identity
from custom_components.mennekes_amtron_rtu.device.register_blocks import REGISTER_BLOCKS
from tests.fakes import FakeModbusClient, device_bank, put, raise_connection_error
from tests.test_network_wire import network_peer, register_reply


@pytest.mark.parametrize("config", [
    SerialConfig("/dev/fake"),
    NetworkConfig("gateway.local", connection_type="rtu_over_tcp"),
    NetworkConfig("gateway.local", connection_type="modbus_tcp"),
])
@pytest.mark.parametrize("replacement", ["OTHER-WALLBOX", ""])
def test_current_write_checks_physical_identity_on_an_open_connection(config, replacement):
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(config, client_factory=lambda _: transport)
        await client.async_connect()
        try:
            await async_read_identity(client)
            put(transport.bank, R.SERIAL_NUMBER, replacement)
            with pytest.raises(AmtronConnectionError):
                await client.async_write_register(R.CHARGING_CURRENT_EMS, 6)
            assert not transport.writes
            assert client.write_state.current_write_generation == 0
            put(transport.bank, R.SERIAL_NUMBER, "ABC123456789")
            await client.async_write_register(R.CHARGING_CURRENT_EMS, 8)
            assert len(transport.writes) == 1
        finally:
            await client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["rtu_over_tcp", "modbus_tcp"])
def test_real_network_reconnect_verifies_identity_before_heartbeat(protocol):
    async def run():
        bank = device_bank()
        async with network_peer(
            protocol, lambda payload, _number: register_reply(payload, bank)
        ) as (port, requests, writers):
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type=protocol, timeout=0.1
            ))
            try:
                await client.async_connect()
                await async_read_identity(client)
                await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
                disconnected = asyncio.Event()
                original = client._client.ctx.trace_connect

                def trace(connected):
                    original(connected)
                    if not connected:
                        disconnected.set()

                client._client.ctx.trace_connect = trace
                put(bank, R.SERIAL_NUMBER, "OTHER-WALLBOX")
                writers[0].close()
                await writers[0].wait_closed()
                await asyncio.wait_for(disconnected.wait(), 1)
                with pytest.raises(AmtronConnectionError):
                    await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
                assert not client.identity_verified
                assert len([p for p in requests if p[1] == 6]) == 1
                put(bank, R.SERIAL_NUMBER, "ABC123456789")
                await client.async_read_register(R.EVSE_STATE)
                assert client.identity_verified
                reads = len([p for p in requests if p[1] == 3])
                for _ in range(2):
                    await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
                assert len([p for p in requests if p[1] == 3]) == reads
                assert len([p for p in requests if p[1] == 6]) == 3
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_initial_heartbeat_rechecks_identity_after_capability_reads():
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        original = transport.read_holding_registers

        async def replace_after_capabilities(address, **kwargs):
            response = await original(address, **kwargs)
            if address == R.MAX_EVSE_CURRENT.address:
                put(transport.bank, R.SERIAL_NUMBER, "OTHER-WALLBOX")
            return response

        transport.read_holding_registers = replace_after_capabilities
        try:
            identity = await async_read_identity(client)
            assert identity.serial_number == "ABC123456789"
            with pytest.raises(AmtronConnectionError):
                await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
            assert not transport.writes
        finally:
            await client.async_close()

    asyncio.run(run())


def test_reconnect_during_verification_cannot_confirm_the_previous_connection():
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        await async_read_identity(client)
        original = transport.read_holding_registers

        async def reconnect_after_read(address, **kwargs):
            response = await original(address, **kwargs)
            client._connection_changed(False)
            client._connection_changed(True)
            return response

        transport.read_holding_registers = reconnect_after_read
        try:
            with pytest.raises(AmtronConnectionError):
                await client.async_write_register(R.CHARGING_CURRENT_EMS, 6)
            assert not client.identity_verified
            assert not transport.writes
        finally:
            await client.async_close()

    asyncio.run(run())


def test_polled_serial_change_blocks_even_periodic_heartbeats():
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        await async_read_identity(client)
        await client.async_read_register(R.EVSE_STATE)
        put(transport.bank, R.SERIAL_NUMBER, "OTHER-WALLBOX")
        block = next(block for block in REGISTER_BLOCKS if block.name == "serial_number")
        try:
            with pytest.raises(AmtronConnectionError):
                await client.async_read_block(block)
            with pytest.raises(AmtronConnectionError):
                await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
            assert not transport.writes
        finally:
            await client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("replacement", [None, "OTHER-WALLBOX"])
def test_binding_cannot_adopt_another_or_missing_serial(replacement):
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        try:
            await async_read_identity(client)
            with pytest.raises(AmtronConnectionError):
                client.bind_identity(replacement)
            assert not client.identity_verified
            client.bind_identity("ABC123456789")
            assert not client.identity_verified
            await client.async_read_register(R.EVSE_STATE)
            assert client.identity_verified
        finally:
            await client.async_close()

    asyncio.run(run())


def test_unanswered_identity_check_is_definitely_before_the_write():
    async def run():
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
        await client.async_connect()
        try:
            await async_read_identity(client)
            transport.read_exceptions[R.SERIAL_NUMBER.address] = raise_connection_error()
            with pytest.raises(AmtronConnectionError):
                await client.async_write_register(R.CHARGING_CURRENT_EMS, 6)
            assert not client.identity_verified
            assert not transport.writes
            assert client.write_state.current_write_generation == 0
            transport.read_exceptions.clear()
            await client.async_write_register(R.CHARGING_CURRENT_EMS, 8)
            assert len(transport.writes) == 1
        finally:
            await client.async_close()

    asyncio.run(run())
