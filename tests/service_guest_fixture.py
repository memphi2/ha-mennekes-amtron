"""Service fixture with a simulated wallbox and no guest-order engine."""
from contextlib import asynccontextmanager

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_guest_runtime import (
    GuestModbusService,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.fakes import put as put

TOKEN = "service-test-credential-32-characters"  # noqa: S105 - fixture


def current_writes(transport):
    return [decode_register(R.CHARGING_CURRENT_EMS, words) for address, words in transport.writes if address == R.CHARGING_CURRENT_EMS.address]


@asynccontextmanager
async def guest_service(tmp_path, *, read_only=False, bank=None):
    transport = FakeModbusClient(bank or device_bank(evse_state=2, charging_current=8))
    clock = [1000.0]
    client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport, monotonic=lambda: clock[0])
    service = GuestModbusService(ServerConfig(client.config, TOKEN, read_only=read_only, current_cap=16), tmp_path / "intent.json", client=client)
    try:
        await service._initialize()
        await service.heartbeat.async_stop()
        transport.writes.clear()
        service.control._monotonic = lambda: clock[0]
        yield service, transport, clock
    finally:
        await service.stop()
