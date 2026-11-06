"""A live service must keep its writes bound to the original physical wallbox."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.fakes import FakeModbusClient, device_bank
from tests.test_modbus_service import TOKEN, current_writes, service_fixture

SERIAL = "ABC123456789"


@pytest.mark.parametrize("protocol", ["rtu_over_tcp", "modbus_tcp"])
@pytest.mark.parametrize("replacement", ["DIFFERENT-WALLBOX", ""])
@pytest.mark.parametrize("operation", ["current", "heartbeat", "recovery"])
@pytest.mark.parametrize("disconnected", [True, False])
def test_service_reconnect_rejects_physical_replacement_before_any_write(
    tmp_path, protocol, replacement, operation, disconnected
):
    async def run():
        clock = [1000.0]
        bus = FakeModbusClient(device_bank(serial=SERIAL, charging_current=8, error_code=200))
        connection = NetworkConfig("gateway.local", connection_type=protocol)
        client = MennekesModbusClient(
            connection, client_factory=lambda _: bus, monotonic=lambda: clock[0]
        )
        service = ModbusService(
            ServerConfig(connection, TOKEN, read_only=False, current_cap=16),
            tmp_path / "intent.json", client=client,
        )
        await service._initialize()
        await service.heartbeat.async_stop()
        saved = service.state_path.read_bytes()
        intent = service.snapshot()["control"]
        bus.writes.clear()
        try:
            # The app process and its cached identity stay alive while the
            # physical endpoint is replaced and the TCP connection drops.
            bus.bank = device_bank(serial=replacement, charging_current=16, error_code=200)
            bus.connected = not disconnected
            if operation == "current":
                with pytest.raises(DeviceError):
                    await service.command({
                        "command": "set_current", "value": 6,
                        "expected_serial_number": SERIAL,
                    })
            elif operation == "heartbeat":
                if not disconnected:
                    # A normal slow identity read also catches a downstream
                    # replacement that leaves the gateway connection open.
                    service._poller._slow_read_at = None
                    with pytest.raises(AmtronConnectionError):
                        await service.refresh()
                assert await service.heartbeat.async_beat_once() is False
            else:
                service.heartbeat._set_healthy(True)
                await service._recover()
            assert bus.writes == []
            assert service.state_path.read_bytes() == saved
            assert service.snapshot()["control"] == intent
            assert service.snapshot()["available"] is False
            assert service.identity.serial_number == SERIAL

            # Reconnecting the original wallbox allows ordinary operation;
            # no entry replacement, saved intent reset, or command replay.
            bus.bank = device_bank(serial=SERIAL, charging_current=8)
            bus.connected = False
            await service.refresh()
            assert service.snapshot()["available"] is True
            assert bus.writes == []
            await service.command({
                "command": "set_current", "value": 10,
                "expected_serial_number": SERIAL,
            })
            assert current_writes(bus) == [10]
        finally:
            await service.stop()

    asyncio.run(run())


def test_physical_identity_rejection_immediately_invalidates_ha_client(tmp_path):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                remote.bind_identity(SERIAL)
                bus.bank = device_bank(serial="DIFFERENT-WALLBOX", charging_current=16)
                bus.writes.clear()
                # An RS-485 device may change without disconnecting its USB
                # adapter. The next explicit write must check actual identity.
                with pytest.raises(DeviceError) as error:
                    await remote.execute("set_current", value=6)
                assert error.value.translation_key == "device_identity_changed"
                assert not remote.connected
                assert remote.failure_reason == "service_identity_changed"
                assert not bus.writes
                assert service.snapshot()["available"] is False
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("command", [
    {"command": "set_current", "value": 6},
    {"command": "pause"},
    {"command": "release", "value": False},
])
def test_cancelled_identity_precheck_does_not_persist_an_unsent_command(tmp_path, command):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        saved = service.state_path.read_bytes()
        intent = service.snapshot()["control"]
        generation = service.client.write_state.current_write_generation
        original = bus.read_holding_registers
        verifying = asyncio.Event()

        async def wait_for_identity(address, **kwargs):
            if address == R.SERIAL_NUMBER.address:
                verifying.set()
                await asyncio.Future()
            return await original(address, **kwargs)

        bus.read_holding_registers = wait_for_identity
        task = asyncio.create_task(service.command({
            **command, "expected_serial_number": SERIAL,
        }))
        try:
            await asyncio.wait_for(verifying.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not bus.writes
            assert service.state_path.read_bytes() == saved
            assert service.snapshot()["control"] == intent
            assert service.client.write_state.current_write_generation == generation
            bus.read_holding_registers = original
            await service.refresh()
            # No current write reached the wire, so there is no new floor.
            await service.command({
                "command": "set_current", "value": 10,
                "expected_serial_number": SERIAL,
            })
            assert current_writes(bus) == [10]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            bus.read_holding_registers = original
            await service.stop()

    asyncio.run(run())
