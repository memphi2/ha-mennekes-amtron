from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import (
    MennekesModbusClient,
    SerialConfig,
    create_serial_client,
)
from custom_components.mennekes_amtron.client_errors import (
    AmtronConnectionError,
    AmtronRateLimitedError,
    AmtronWriteRejectedError,
)
from custom_components.mennekes_amtron.register_blocks import REGISTER_BLOCKS
from tests.fakes import FakeModbusClient, device_bank

CONFIG = SerialConfig(port="/dev/fake", device_id=50)


def _client(
    transport: FakeModbusClient, monotonic: list[float] | None = None
) -> MennekesModbusClient:
    clock = monotonic if monotonic is not None else [0.0]
    return MennekesModbusClient(
        CONFIG,
        client_factory=lambda _config: transport,
        monotonic=lambda: clock[0],
    )


def test_connect_opens_the_transport_once() -> None:
    transport = FakeModbusClient()

    async def run() -> None:
        client = _client(transport)
        assert not client.connected
        await client.async_connect()
        await client.async_connect()
        assert client.connected
        await client.async_close()
        assert transport.close_calls == 1

    asyncio.run(run())


def test_a_refused_port_raises_a_connection_error() -> None:
    transport = FakeModbusClient()
    transport.connect_result = False

    async def run() -> None:
        with pytest.raises(AmtronConnectionError, match="/dev/fake"):
            await _client(transport).async_connect()

    asyncio.run(run())


def test_an_oserror_from_pyserial_becomes_a_connection_error() -> None:
    transport = FakeModbusClient()
    transport.connect_error = OSError("no such device")

    async def run() -> None:
        with pytest.raises(AmtronConnectionError, match="no such device"):
            await _client(transport).async_connect()

    asyncio.run(run())


def test_closing_an_unopened_client_is_a_no_op() -> None:
    asyncio.run(_client(FakeModbusClient()).async_close())


def test_reading_without_a_connection_is_refused() -> None:
    async def run() -> None:
        with pytest.raises(AmtronConnectionError, match="not connected"):
            await _client(FakeModbusClient()).async_read_register(R.EVSE_STATE)

    asyncio.run(run())


def test_block_and_register_reads_decode_device_words() -> None:
    transport = FakeModbusClient(device_bank())
    block = next(block for block in REGISTER_BLOCKS if block.name == "status")

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        assert await client.async_read_block(block) == {
            "evse_state": 1,
            "authorization_status": 1,
            "downgrade_status": 1,
            "phase_rotation": 0,
        }
        assert await client.async_read_register(R.MAX_EVSE_CURRENT) == 16.0
        assert await client.async_read_words(address=0x0100, count=2) == [1, 1]

    asyncio.run(run())


def test_the_bus_lock_serializes_overlapping_reads() -> None:
    transport = FakeModbusClient(device_bank())
    transport.read_delay = 0.01

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        await asyncio.gather(
            *(client.async_read_register(R.EVSE_STATE) for _ in range(5))
        )
        assert transport.max_concurrent == 1

    asyncio.run(run())


def test_writes_go_out_as_the_documented_function_codes() -> None:
    transport = FakeModbusClient(device_bank())

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        await client.async_write_register(R.CHARGING_RELEASE, 1)
        await client.async_write_register(R.CHARGING_CURRENT_EMS, 6.0)
        assert transport.writes[0] == (R.CHARGING_RELEASE.address, [1])
        assert len(transport.writes[1][1]) == 2

    asyncio.run(run())


def test_a_write_inside_the_interval_is_rate_limited() -> None:
    transport = FakeModbusClient(device_bank())
    clock = [100.0]

    async def run() -> None:
        client = _client(transport, clock)
        await client.async_connect()
        await client.async_write_register(
            R.CHARGING_CURRENT_EMS, 6.0, min_interval=5.0
        )
        with pytest.raises(AmtronRateLimitedError) as excinfo:
            await client.async_write_register(
                R.CHARGING_CURRENT_EMS, 7.0, min_interval=5.0
            )
        assert excinfo.value.retry_after == pytest.approx(5.0)
        clock[0] += 5.0
        await client.async_write_register(
            R.CHARGING_CURRENT_EMS, 8.0, min_interval=5.0
        )
        assert len(transport.writes) == 2

    asyncio.run(run())


def test_a_failed_write_does_not_start_the_rate_limit() -> None:
    transport = FakeModbusClient(device_bank())
    transport.write_exceptions[R.CHARGING_RELEASE.address] = 0x01

    async def run() -> None:
        client = _client(transport)
        await client.async_connect()
        with pytest.raises(AmtronWriteRejectedError):
            await client.async_write_register(
                R.CHARGING_RELEASE, 1, min_interval=5.0
            )
        transport.write_exceptions.clear()
        await client.async_write_register(R.CHARGING_RELEASE, 1, min_interval=5.0)
        assert transport.writes == [(R.CHARGING_RELEASE.address, [1])]

    asyncio.run(run())


def test_the_real_factory_builds_an_rtu_client() -> None:
    async def run() -> None:
        client = create_serial_client(
            SerialConfig(port="/dev/null", baudrate=57600, stopbits=2)
        )
        assert client.comm_params.baudrate == 57600
        assert client.comm_params.stopbits == 2
        assert client.comm_params.comm_name

    asyncio.run(run())


def test_a_transport_without_close_is_tolerated() -> None:
    class NoClose(FakeModbusClient):
        close = None

    async def run() -> None:
        transport = NoClose()
        client = _client(transport)
        await client.async_connect()
        await client.async_close()

    asyncio.run(run())


def test_an_async_close_is_awaited() -> None:
    class AsyncClose(FakeModbusClient):
        async def close(self) -> None:  # type: ignore[override]
            self.close_calls += 1
            self.connected = False

    async def run() -> None:
        transport = AsyncClose()
        client = _client(transport)
        await client.async_connect()
        await client.async_close()
        assert transport.close_calls == 1

    asyncio.run(run())
