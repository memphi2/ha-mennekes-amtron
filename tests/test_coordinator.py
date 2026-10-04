from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.coordinator import MennekesAmtronCoordinator
from custom_components.mennekes_amtron.data import ConnectionState, DeviceIdentity
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.ha_fakes import FakeConfigEntry, FakeHass


async def _coordinator(
    transport: FakeModbusClient,
    *,
    layout: int = R.LAYOUT_V01_03,
    connection_state: ConnectionState | None = None,
) -> MennekesAmtronCoordinator:
    client = MennekesModbusClient(
        SerialConfig(port="/dev/fake"), client_factory=lambda _config: transport
    )
    await client.async_connect()
    return MennekesAmtronCoordinator(
        FakeHass(),
        FakeConfigEntry(),
        client=client,
        identity=DeviceIdentity(layout_version=layout),
        connection_state=connection_state or ConnectionState(),
        scan_interval=5,
    )


def test_a_poll_reads_every_supported_block() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport)
        data = await coordinator._async_update_data()
        assert data.failed_blocks == ()
        assert data.get("evse_state") == 1
        assert data.get("energy_total") == pytest.approx(1234.5)
        assert coordinator.identity.layout_version == R.LAYOUT_V01_03
        assert len(transport.reads) == 18

    asyncio.run(run())


def test_an_older_layout_polls_fewer_blocks() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank(layout=R.LAYOUT_V01_00))
        coordinator = await _coordinator(transport, layout=R.LAYOUT_V01_00)
        data = await coordinator._async_update_data()
        assert data.get("energy_total") is None
        assert len(transport.reads) < 18

    asyncio.run(run())


def test_a_single_failing_block_does_not_lose_the_snapshot() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        transport.read_exceptions[0x1000] = 0x04
        coordinator = await _coordinator(transport)
        data = await coordinator._async_update_data()
        assert data.failed_blocks == ("statistics",)
        assert data.get("evse_state") == 1

    asyncio.run(run())


def test_a_lost_connection_fails_the_update() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        transport.read_exceptions[0x0000] = raise_connection_error()
        coordinator = await _coordinator(transport)
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_an_entirely_silent_device_fails_the_update() -> None:
    async def run() -> None:
        transport = FakeModbusClient()
        for block_address in (
            0x0000,
            0x0013,
            0x001B,
            0x0100,
            0x0108,
            0x0114,
            0x0300,
            0x030A,
            0x030C,
            0x030D,
            0x0500,
            0x0900,
            0x0B00,
            0x0B06,
            0x0D02,
            0x0E00,
            0x0E01,
            0x1000,
        ):
            transport.read_exceptions[block_address] = 0x04
        coordinator = await _coordinator(transport)
        with pytest.raises(UpdateFailed, match="no register block"):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_availability_is_logged_once_per_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        state = ConnectionState()
        coordinator = await _coordinator(transport, connection_state=state)
        await coordinator._async_update_data()

        transport.read_exceptions[0x0000] = raise_connection_error()
        with caplog.at_level(logging.INFO):
            for _ in range(3):
                with pytest.raises(UpdateFailed):
                    await coordinator._async_update_data()
            assert sum("Lost the connection" in r.message for r in caplog.records) == 1

            transport.read_exceptions.clear()
            await coordinator._async_update_data()
            await coordinator._async_update_data()
            assert sum("Reconnected" in r.message for r in caplog.records) == 1

    asyncio.run(run())


def test_current_data_is_empty_before_the_first_poll() -> None:
    async def run() -> None:
        coordinator = await _coordinator(FakeModbusClient(device_bank()))
        assert coordinator.current_data().values == {}

    asyncio.run(run())
