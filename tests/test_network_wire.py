"""Exercise both LAN framings with real sockets and independent wire replies."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.capabilities import supported_blocks
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronProtocolError,
    AmtronRateLimitedError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.const import HEARTBEAT_VALUE
from custom_components.mennekes_amtron_rtu.device.identity import async_read_identity
from tests.fakes import device_bank
from tests.test_serial_faults import frame

PROTOCOLS = ["rtu_over_tcp", "modbus_tcp"]


def register_reply(payload, bank):
    """Decode only the documented functions, without the client's Modbus library."""
    unit, function = payload[:2]
    assert unit == 50
    address = int.from_bytes(payload[2:4], "big")
    if function == 3:
        count = int.from_bytes(payload[4:6], "big")
        return bytes([unit, function, count * 2]) + b"".join(
            bank.get(address + offset, 0).to_bytes(2, "big")
            for offset in range(count)
        )
    if function == 6:
        bank[address] = int.from_bytes(payload[4:6], "big")
        return payload
    assert function == 16
    count = int.from_bytes(payload[4:6], "big")
    assert payload[6] == count * 2
    assert len(payload) == 7 + count * 2
    for offset in range(count):
        bank[address + offset] = int.from_bytes(
            payload[7 + offset * 2:9 + offset * 2], "big"
        )
    return payload[:6]


@asynccontextmanager
async def network_peer(protocol, reply, *, fragment=False):
    """Serve one gateway endpoint, track all requests and clean up every socket."""
    requests = []
    tasks = []
    writers = []

    async def handle(reader, writer):
        writers.append(writer)
        try:
            while True:
                if protocol == "modbus_tcp":
                    header = await reader.readexactly(7)
                    assert header[2:4] == b"\x00\x00"
                    length = int.from_bytes(header[4:6], "big")
                    assert 2 <= length <= 254
                    payload = header[6:] + await reader.readexactly(length - 1)
                else:
                    prefix = await reader.readexactly(6)
                    if prefix[1] == 16:
                        count = await reader.readexactly(1)
                        prefix += count + await reader.readexactly(count[0])
                    crc = await reader.readexactly(2)
                    assert frame(prefix) == prefix + crc
                    payload = prefix
                requests.append(payload)
                response = reply(payload, len(requests))
                if response is None:
                    continue
                wire = (
                    header[:4] + len(response).to_bytes(2, "big") + response
                    if protocol == "modbus_tcp" else frame(response)
                )
                if fragment:
                    writer.write(wire[:3])
                    await writer.drain()
                    await asyncio.sleep(0.002)
                    wire = wire[3:]
                writer.write(wire)
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        tasks.append(asyncio.create_task(handle(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], requests, writers
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await asyncio.gather(*tasks)
        await server.wait_closed()


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_network_identity_polling_current_and_heartbeat_with_fragmented_replies(protocol):
    async def run():
        bank = device_bank()
        async with network_peer(
            protocol, lambda payload, _number: register_reply(payload, bank), fragment=True
        ) as (port, requests, _writers):
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type=protocol
            ))
            try:
                await client.async_connect()
                identity = await async_read_identity(client)
                assert identity.serial_number == "ABC123456789"
                assert identity.article_number == "1313201205"
                assert identity.max_evse_current == 16
                values = {}
                for block in supported_blocks(identity.layout_version):
                    values.update(await client.async_read_block(block))
                assert values["voltage_l1"] == pytest.approx(230.1)
                assert values["energy_total"] == pytest.approx(1234.5)
                await client.async_write_register(R.CHARGING_CURRENT_EMS, 6.78)
                assert await client.async_read_register(R.CHARGING_CURRENT_EMS) == pytest.approx(6.78)
                await client.async_write_register(R.HEARTBEAT, HEARTBEAT_VALUE, priority=True)
                assert bank[R.HEARTBEAT.address] == HEARTBEAT_VALUE
                assert {payload[1] for payload in requests} == {3, 6, 16}
                assert client.write_trace.snapshot() == []
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 15))


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_network_lost_write_ack_preserves_floor_without_repeating_applied_write(protocol):
    async def run():
        bank = device_bank()

        def reply(payload, _number):
            response = register_reply(payload, bank)
            return None if payload[1] == 16 else response

        async with network_peer(protocol, reply) as (port, requests, _writers):
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type=protocol, timeout=0.04
            ))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 8)
                failed, = client.write_trace.snapshot()
                assert failed["outcome"] == "io_error"
                assert failed["tx_packets"] == 1
                assert failed["rx_callbacks"] == 0
                with pytest.raises(AmtronRateLimitedError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 10)
                assert len(requests) == 1
                # The write really arrived despite its missing acknowledgement.
                assert await client.async_read_register(R.CHARGING_CURRENT_EMS) == 8
                await client.async_write_register(R.HEARTBEAT, HEARTBEAT_VALUE, priority=True)
                assert [payload[1] for payload in requests] == [16, 3, 6]
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_network_read_timeouts_use_only_read_budget_and_release_bus_for_heartbeat(protocol):
    async def run():
        async with network_peer(
            protocol, lambda payload, _number: payload if payload[1] == 6 else None
        ) as (port, requests, _writers):
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type=protocol, timeout=0.04
            ))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_read_register(R.EVSE_STATE)
                assert [payload[1] for payload in requests] == [3, 3, 3]
                await client.async_write_register(R.HEARTBEAT, HEARTBEAT_VALUE, priority=True)
                assert [payload[1] for payload in requests] == [3, 3, 3, 6]
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("next_operation", ["connect", "read", "heartbeat"])
def test_network_gateway_disconnect_can_reconnect_and_read_again(protocol, next_operation):
    async def run():
        bank = device_bank()
        async with network_peer(
            protocol, lambda payload, _number: register_reply(payload, bank)
        ) as (port, requests, writers):
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type=protocol, timeout=0.2
            ))
            try:
                await client.async_connect()
                assert await client.async_read_register(R.EVSE_STATE) == 1
                disconnected = asyncio.Event()
                client._client.ctx.trace_connect = (
                    lambda connected: None if connected else disconnected.set()
                )
                writers[0].close()
                await writers[0].wait_closed()
                # Let the reader observe EOF before reconnecting the same client.
                await disconnected.wait()
                if next_operation == "connect":
                    await client.async_connect()
                if next_operation == "heartbeat":
                    await client.async_write_register(R.HEARTBEAT, HEARTBEAT_VALUE, priority=True)
                else:
                    assert await client.async_read_register(R.EVSE_STATE) == 1
                assert len(writers) == 2
                assert len(requests) == 2
            finally:
                await client.async_close()
            # A library reconnect racing our explicit reconnect must not leave
            # an extra socket alive after the integration releases ownership.
            async with asyncio.timeout(1):
                await asyncio.gather(*(writer.wait_closed() for writer in writers))

    asyncio.run(asyncio.wait_for(run(), 10))
