"""Exercise lost replies and cancellation through the real serial library."""

from __future__ import annotations

import asyncio
import os
import pty
from contextlib import contextmanager

import pytest

from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu._flow_search import async_probe, async_search
from custom_components.mennekes_amtron_rtu.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.client_errors import (
    AmtronProtocolError,
    AmtronRateLimitedError,
)


@contextmanager
def silent_serial():
    """Capture actual RTU bytes without replying, and release every descriptor."""

    master, slave = pty.openpty()
    os.set_blocking(master, False)
    frames = []
    received = asyncio.Event()
    loop = asyncio.get_running_loop()

    def capture():
        data = os.read(master, 4096)
        if data:
            frames.append(data)
            received.set()

    loop.add_reader(master, capture)
    try:
        yield os.ttyname(slave), frames, received
    finally:
        loop.remove_reader(master)
        os.close(master)
        os.close(slave)


def test_lost_write_reply_never_retransmits_inside_the_current_cooldown():
    async def run():
        with silent_serial() as (port, frames, _received):
            clock = [0.0]
            client = MennekesModbusClient(
                SerialConfig(port=port, timeout=0.05), monotonic=lambda: clock[0]
            )
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 8)
                assert len(frames) == 1
                assert frames[0][1:4] == bytes([0x10, 0x03, 0x02])
                with pytest.raises(AmtronRateLimitedError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 10)
                assert len(frames) == 1
                clock[0] = 5.0
                with pytest.raises(AmtronProtocolError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 10)
                assert len(frames) == 2
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 10))


def test_unanswered_reads_keep_the_configured_retry_budget():
    async def run():
        with silent_serial() as (port, frames, _received):
            client = MennekesModbusClient(SerialConfig(port=port, timeout=0.05))
            try:
                await client.async_connect()
                with pytest.raises(AmtronProtocolError):
                    await client.async_read_register(R.EVSE_STATE)
                assert len(frames) == 3
                assert all(frame[1] == 0x03 for frame in frames)
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 10))


def test_cancelling_an_inflight_probe_stops_search_and_releases_the_port():
    async def run():
        with silent_serial() as (port, frames, received):
            attempts = []

            async def probe(config):
                attempts.append(config.device_id)
                return await async_probe(config)

            task = asyncio.create_task(
                async_search(SerialConfig(port=port), all_parameters=False, probe=probe)
            )
            await asyncio.wait_for(received.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert attempts == [50]
            assert len(frames) == 1
            # A new exclusive serial client must be able to own the port now.
            replacement = MennekesModbusClient(SerialConfig(port=port))
            try:
                await replacement.async_connect()
                assert replacement.connected
            finally:
                await replacement.async_close()

    asyncio.run(asyncio.wait_for(run(), 10))


def test_cancelled_write_propagates_and_retains_the_uncertain_write_floor():
    async def run():
        with silent_serial() as (port, frames, received):
            client = MennekesModbusClient(SerialConfig(port=port))
            try:
                await client.async_connect()
                task = asyncio.create_task(
                    client.async_write_register(R.CHARGING_CURRENT_EMS, 8)
                )
                await asyncio.wait_for(received.wait(), 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                with pytest.raises(AmtronRateLimitedError):
                    await client.async_write_register(R.CHARGING_CURRENT_EMS, 10)
                assert len(frames) == 1
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 10))
