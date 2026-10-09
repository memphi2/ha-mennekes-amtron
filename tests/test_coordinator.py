from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.coordinator import MennekesAmtronCoordinator
from custom_components.mennekes_amtron.data import ConnectionState, DeviceIdentity
from custom_components.mennekes_amtron.enums import EvseState
from custom_components.mennekes_amtron.register_blocks import (
    REGISTER_BLOCKS,
    SLOW_BLOCK_INTERVAL_SECONDS,
    BlockCadence,
)
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.ha_fakes import FakeConfigEntry, FakeHass


async def _coordinator(
    transport: FakeModbusClient,
    *,
    layout: int = R.LAYOUT_V01_03,
    connection_state: ConnectionState | None = None,
    monotonic: Any = None,
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
        **({"monotonic": monotonic} if monotonic else {}),
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
        transport.read_exceptions[0x0100] = raise_connection_error()
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

        # a block read on every poll, not one of the slow configuration ones
        transport.read_exceptions[0x0100] = raise_connection_error()
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


def test_configuration_blocks_are_not_read_on_every_poll() -> None:
    """They change when somebody reconfigures the wallbox, not while charging."""

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        clock = [1000.0]
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])

        await coordinator.async_refresh()
        first = len(transport.reads)
        assert first == len(REGISTER_BLOCKS)

        transport.reads.clear()
        await coordinator.async_refresh()
        fast = {
            block.address
            for block in REGISTER_BLOCKS
            if block.cadence is BlockCadence.FAST
        }
        assert {address for address, _ in transport.reads} == fast
        assert len(transport.reads) < first

        # the configuration values survive a poll that did not read them
        assert coordinator.data.get("serial_number") == "ABC123456789"
        assert coordinator.data.get("ems_fallback_current") == 6

        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS
        transport.reads.clear()
        await coordinator.async_refresh()
        assert len(transport.reads) == len(REGISTER_BLOCKS)

    asyncio.run(run())


def test_a_configuration_change_is_picked_up_at_the_slow_cadence() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        clock = [1000.0]
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        await coordinator.async_refresh()
        assert coordinator.data.get("ems_fallback_current") == 6

        transport.bank[R.EMS_FALLBACK_CURRENT.address] = 16
        await coordinator.async_refresh()
        assert coordinator.data.get("ems_fallback_current") == 6

        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS
        await coordinator.async_refresh()
        assert coordinator.data.get("ems_fallback_current") == 16

    asyncio.run(run())


def test_a_failing_block_drops_its_values_instead_of_freezing_them() -> None:
    """A block that was read and failed has no current value.

    Carrying the last one forward made the entities report a frozen
    measurement as a live one, which an automation cannot tell apart from a
    real reading.
    """

    async def run() -> None:
        # A connected vehicle keeps the measurement block on the fast cadence;
        # this test is about the failure path, not about the cadence.
        transport = FakeModbusClient(device_bank(evse_state=EvseState.CHARGING))
        coordinator = await _coordinator(transport)
        first = await coordinator._async_update_data()
        assert first.has("voltage_l1")
        coordinator.data = first

        transport.read_exceptions[0x0500] = 0x04
        second = await coordinator._async_update_data()
        assert second.failed_blocks == ("measurements",)
        assert not second.has("voltage_l1")
        assert second.get("voltage_l1") is None
        # The blocks that did answer are untouched.
        assert second.get("evse_state") == EvseState.CHARGING

    asyncio.run(run())


def test_a_slow_block_that_is_not_due_keeps_its_values() -> None:
    """Dropping stale values must not drop the ones carried on purpose."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.has("energy_total")

        clock[0] += 1
        later = await coordinator._async_update_data()
        slow = [
            block
            for block in REGISTER_BLOCKS
            if block.cadence is BlockCadence.SLOW
        ]
        assert slow
        assert later.failed_blocks == ()
        assert later.has("energy_total")

    asyncio.run(run())


def test_a_failing_slow_block_drops_its_values_when_it_was_due() -> None:
    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.has("energy_total")

        transport.read_exceptions[0x1000] = 0x04
        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS + 1
        later = await coordinator._async_update_data()
        assert later.failed_blocks == ("statistics",)
        assert not later.has("energy_total")

    asyncio.run(run())


# --- the measurement blocks follow the vehicle, not the clock --------------


def _measurement_reads(transport: FakeModbusClient) -> int:
    measurements = next(
        block for block in REGISTER_BLOCKS if block.name == "measurements"
    )
    return sum(
        1 for address, _count in transport.reads if address == measurements.address
    )


def test_an_idle_wallbox_stops_polling_the_measurements() -> None:
    """Three voltages read every few seconds are a database row every few
    seconds, describing a wallbox that has nothing plugged into it."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.IDLE))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 1

        for _ in range(5):
            clock[0] += 5
            coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 1
        # The values stay in the snapshot, so the entities stay available.
        assert coordinator.data.has("voltage_l1")

    asyncio.run(run())


def test_a_connected_vehicle_keeps_the_measurements_on_the_fast_cadence() -> None:
    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.CHARGING))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        for _ in range(6):
            clock[0] += 5
            coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 6

    asyncio.run(run())


def test_a_vehicle_that_is_only_plugged_in_already_counts() -> None:
    """Everything above idle means something is connected."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.EV_CONNECTED))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        clock[0] += 5
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 2

    asyncio.run(run())


def test_an_unknown_state_is_treated_as_connected() -> None:
    """Being wrong this way costs bus traffic; the other way hides a charge."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        # No poll has happened, so the snapshot carries no state at all.
        assert coordinator._vehicle_is_connected()
        transport.read_exceptions[0x0100] = 0x04
        coordinator.data = await coordinator._async_update_data()
        clock[0] += 5
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 2

    asyncio.run(run())


def test_an_idle_wallbox_still_refreshes_the_measurements_every_minute() -> None:
    """Demoted, not dropped: the mains voltage stays visible."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.IDLE))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS + 1
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 2

    asyncio.run(run())
