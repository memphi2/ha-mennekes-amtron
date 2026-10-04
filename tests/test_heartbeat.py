from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.data import WriteDiagnostics
from custom_components.mennekes_amtron.heartbeat import HeartbeatTask
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error

CONFIG = SerialConfig(port="/dev/fake", device_id=50)


def _client(transport: FakeModbusClient) -> MennekesModbusClient:
    return MennekesModbusClient(CONFIG, client_factory=lambda _config: transport)


def test_one_beat_writes_the_documented_magic_value() -> None:
    transport = FakeModbusClient(device_bank())
    diagnostics = WriteDiagnostics()

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        assert await HeartbeatTask(client, diagnostics).async_beat_once()

    asyncio.run(run())
    assert transport.writes == [(R.HEARTBEAT.address, [0x55AA])]
    assert diagnostics.heartbeats_sent == 1


def test_a_failed_beat_is_counted_and_swallowed() -> None:
    transport = FakeModbusClient(device_bank())
    transport.write_exceptions[R.HEARTBEAT.address] = raise_connection_error()
    diagnostics = WriteDiagnostics()

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        assert not await HeartbeatTask(client, diagnostics).async_beat_once()

    asyncio.run(run())
    assert diagnostics.heartbeats_failed == 1
    assert diagnostics.last_error


def test_the_task_keeps_beating_while_the_poll_fails() -> None:
    """A failing data poll must never be able to starve the heartbeat."""

    transport = FakeModbusClient(device_bank())
    transport.read_exceptions[0x0500] = raise_connection_error()
    diagnostics = WriteDiagnostics()

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        heartbeat = HeartbeatTask(
            client, diagnostics, interval=0, sleep=_counting_sleep(4)
        )
        heartbeat.start()
        heartbeat.start()
        await asyncio.sleep(0)
        for _ in range(20):
            if diagnostics.heartbeats_sent >= 4:
                break
            await asyncio.sleep(0)
        await heartbeat.async_stop()
        assert not heartbeat.running

    asyncio.run(run())
    assert diagnostics.heartbeats_sent >= 4


def test_stopping_cancels_the_task() -> None:
    transport = FakeModbusClient(device_bank())

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        heartbeat = HeartbeatTask(client, WriteDiagnostics(), interval=3600)
        heartbeat.start()
        await asyncio.sleep(0)
        assert heartbeat.running
        await heartbeat.async_stop()
        assert not heartbeat.running
        await heartbeat.async_stop()

    asyncio.run(run())


def test_an_adopted_task_is_cancelled_too() -> None:
    transport = FakeModbusClient(device_bank())

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        heartbeat = HeartbeatTask(client, WriteDiagnostics(), interval=3600)
        task = asyncio.get_running_loop().create_task(heartbeat.async_run())
        heartbeat.attach(task)
        await asyncio.sleep(0)
        await heartbeat.async_stop()
        assert task.cancelled()

    asyncio.run(run())


def _counting_sleep(limit: int):
    beats = {"count": 0}

    async def sleep(_delay: float) -> None:
        beats["count"] += 1
        if beats["count"] >= limit:
            await asyncio.sleep(3600)
        await asyncio.sleep(0)

    return sleep
