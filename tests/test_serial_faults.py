"""Inject faulty RTU bytes through a real serial client, without hardware."""

import asyncio
import os
import pty
from contextlib import asynccontextmanager

import pytest

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.client_errors import (
    AmtronProtocolError,
    AmtronRateLimitedError,
)


def frame(payload):
    """Append the Modbus CRC; independent of the parser under test."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


def test_crc_reference_vector():
    assert frame(bytes.fromhex("01 03 00 00 00 0A")) == bytes.fromhex("01 03 00 00 00 0A C5 CD")


@asynccontextmanager
async def serial_peer(reply):
    """Frame requests and send scripted fragments; close all tasks and FDs."""
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    loop = asyncio.get_running_loop()
    buffer = bytearray()
    requests = []
    tasks = []

    async def send(chunks):
        for delay, chunk in chunks:
            await asyncio.sleep(delay)
            os.write(master, chunk)

    def receive():
        buffer.extend(os.read(master, 4096))
        while len(buffer) >= 8:
            length = 9 + buffer[6] if buffer[1] == 0x10 else 8
            if len(buffer) < length:
                return
            request = bytes(buffer[:length])
            del buffer[:length]
            requests.append(request)
            tasks.append(asyncio.create_task(send(reply(request, len(requests)))))

    loop.add_reader(master, receive)
    try:
        yield os.ttyname(slave), requests
    finally:
        loop.remove_reader(master)
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        os.close(master)
        os.close(slave)
        assert all(result is None or isinstance(result, asyncio.CancelledError) for result in results)


def test_fragmented_reply_and_foreign_device_reply_do_not_corrupt_the_read():
    correct = frame(bytes.fromhex("32 03 02 00 05"))

    def reply(_request, _number):
        foreign = frame(bytes.fromhex("33 03 02 FF FF"))
        return [(0, foreign), (0.005, correct[:3]), (0.005, correct[3:])]

    async def run():
        async with serial_peer(reply) as (port, requests):
            client = MennekesModbusClient(SerialConfig(port=port, timeout=0.1))
            try:
                await client.async_connect()
                assert await client.async_read_register(R.EVSE_STATE) == 5
                assert len(requests) == 1
            finally:
                await client.async_close()
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("fault", ["crc", "truncated", "late"])
def test_bad_reads_exhaust_only_the_read_budget_then_heartbeat_can_use_the_bus(fault):
    response = frame(bytes.fromhex("32 03 02 00 05"))

    def reply(request, _number):
        if request[1] == 0x06:
            return [(0, request)]
        if fault == "late":
            # Arrives after the complete retry budget, before a new request.
            return [(0.2, response)]
        return [(0, response[:-1] if fault == "truncated" else response[:-1] + bytes([response[-1] ^ 1]))]

    async def run():
        async with serial_peer(reply) as (port, requests):
            client = MennekesModbusClient(SerialConfig(port=port, timeout=0.02))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_read_register(R.EVSE_STATE)
                assert len(requests) == 3
                if fault == "late":
                    await asyncio.sleep(0.25)
                await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
                assert len(requests) == 4
                assert requests[-1][1] == 0x06
            finally:
                await client.async_close()
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("payload", [
    "32 10 03 03 00 02",  # Acknowledges a different address.
    "32 10 03 02 00 01",  # Acknowledges too few words.
    "32 06 03 02 00 02",  # Wrong function, plausible address/data.
    "32 83 02",           # Exception for a read, not this write.
])
def test_mismatched_write_acknowledgement_cannot_confirm_or_unlock_a_write(payload):
    def reply(_request, _number):
        return [(0, frame(bytes.fromhex(payload)))]

    async def run():
        async with serial_peer(reply) as (port, requests):
            client = MennekesModbusClient(SerialConfig(port=port, timeout=0.05))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 8)
                with pytest.raises(AmtronRateLimitedError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 10)
                assert len(requests) == 1
            finally:
                await client.async_close()
    asyncio.run(asyncio.wait_for(run(), 10))


def test_wrong_single_write_value_cannot_confirm_a_heartbeat():
    def reply(request, _number):
        return [(0, frame(request[:4] + b"\x00\x00"))]

    async def run():
        async with serial_peer(reply) as (port, requests):
            client = MennekesModbusClient(SerialConfig(port=port, timeout=0.05))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
                assert len(requests) == 1
            finally:
                await client.async_close()
    asyncio.run(asyncio.wait_for(run(), 10))


@pytest.mark.parametrize("payload", ["32 06 0D 00 00 05", "32 86 02"])
def test_stale_write_response_cannot_become_a_read_value(payload):
    def reply(_request, _number):
        return [(0, frame(bytes.fromhex(payload)))]

    async def run():
        async with serial_peer(reply) as (port, requests):
            client = MennekesModbusClient(SerialConfig(port=port, timeout=0.05))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_read_register(R.EVSE_STATE)
                assert len(requests) == 1
            finally:
                await client.async_close()
    asyncio.run(asyncio.wait_for(run(), 10))
