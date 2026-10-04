"""An end-to-end test over a real serial link.

Every other test replaces the pymodbus transport. This one does not: it runs
a real Modbus RTU server at the far end of a virtual serial link and drives
it with the integration's own client, at the wallbox's factory bus settings
(57600 baud, 8N2, device address 50).

That is as close to the real device as this repository can get without
hardware, and it is the only place where framing, byte order and the
keyword-only pymodbus call signatures are proven together rather than
assumed.
"""

from __future__ import annotations

import asyncio
import os
import pty
import threading

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.capabilities import supported_blocks
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.const import HEARTBEAT_VALUE
from custom_components.mennekes_amtron.identity import async_read_identity
from tests.fakes import device_bank

# Only the server-side API differs between pymodbus releases; the client API
# this test exercises is identical in every version the supported Home
# Assistant range resolves, and the unit tests cover it on all of them.
_simdata = pytest.importorskip(
    "pymodbus.simulator.simdata",
    reason="this pymodbus build ships no simulator to run a server from",
)
if not hasattr(_simdata, "DataType"):
    pytest.skip(
        "pymodbus older than 3.13 has a different server-side simulator API",
        allow_module_level=True,
    )

DEVICE_ID = 50
REGISTER_COUNT = 0x1010


def _virtual_serial_link() -> tuple[str, str]:
    """Return two serial paths that are wired to each other."""

    master_a, slave_a = pty.openpty()
    master_b, slave_b = pty.openpty()

    def relay(source: int, target: int) -> None:
        while True:
            try:
                data = os.read(source, 4096)
            except OSError:
                return
            if not data:
                return
            try:
                os.write(target, data)
            except OSError:
                return

    for source, target in ((master_a, master_b), (master_b, master_a)):
        threading.Thread(target=relay, args=(source, target), daemon=True).start()
    return os.ttyname(slave_a), os.ttyname(slave_b)


def _signed(word: int) -> int:
    return word - 0x10000 if word > 0x7FFF else word


def _server_context(words: list[int]) -> object:
    """Return a Modbus server context holding the wallbox register image."""

    from pymodbus.simulator import SimData, SimDevice

    registers = SimData(0, values=words, datatype=_simdata.DataType.REGISTERS)
    return [SimDevice(DEVICE_ID, simdata=[registers])]


def test_the_integration_drives_a_real_modbus_rtu_server() -> None:
    from pymodbus import FramerType
    from pymodbus.server import ModbusSerialServer

    bank = device_bank()
    device_port, client_port = _virtual_serial_link()

    async def run() -> None:
        words = [_signed(bank.get(address, 0)) for address in range(REGISTER_COUNT)]
        server = ModbusSerialServer(
            _server_context(words),
            framer=FramerType.RTU,
            port=device_port,
            baudrate=57600,
            bytesize=8,
            parity="N",
            stopbits=2,
        )
        serving = asyncio.get_running_loop().create_task(server.serve_forever())
        await asyncio.sleep(1.0)

        client = MennekesModbusClient(
            SerialConfig(
                port=client_port,
                baudrate=57600,
                bytesize=8,
                parity="N",
                stopbits=2,
                device_id=DEVICE_ID,
                timeout=2.0,
            )
        )
        try:
            await client.async_connect()

            identity = await async_read_identity(client)
            assert identity.layout_version == R.LAYOUT_V01_03
            assert identity.serial_number == "ABC123456789"
            assert identity.article_number == "1313201205"
            assert identity.phase_options_hw == 2
            assert identity.max_evse_current == 16.0

            values: dict[str, object] = {}
            for block in supported_blocks(identity.layout_version):
                values.update(await client.async_read_block(block))
            assert values["evse_state"] == 1
            assert values["voltage_l1"] == pytest.approx(230.1)
            assert values["energy_total"] == pytest.approx(1234.5)
            assert values["session_duration"] == 600
            assert values["session_energy"] == pytest.approx(1.25)

            # float32 over two registers, written with function 0x10.
            await client.async_write_register(R.CHARGING_CURRENT_EMS, 6.78)
            assert await client.async_read_register(
                R.CHARGING_CURRENT_EMS
            ) == pytest.approx(6.78)

            # uint16 heartbeat, written with function 0x06.
            await client.async_write_register(R.HEARTBEAT, HEARTBEAT_VALUE)
        finally:
            await client.async_close()
            await server.shutdown()
            serving.cancel()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
