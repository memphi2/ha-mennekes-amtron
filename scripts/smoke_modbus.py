#!/usr/bin/env python3
"""Read-only bus smoke test for an AMTRON wallbox.

Run this before the integration writes anything. It opens the port, reads the
identity registers and prints them. It never writes, so it cannot put the
wallbox into the energy-manager error state, and it proves the wiring, the
bus parameters and the device address on their own.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.mennekes_amtron.client import (  # noqa: E402
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron.client_errors import (  # noqa: E402
    AmtronBusError,
)
from custom_components.mennekes_amtron.const import (  # noqa: E402
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
)
from custom_components.mennekes_amtron.identity import (  # noqa: E402
    async_read_identity,
)
from custom_components.mennekes_amtron.registers import (  # noqa: E402
    MAX_EVSE_CURRENT,
    layout_label,
)


def main() -> int:
    """Parse the bus parameters and run the read-only smoke test."""

    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="Serial port, e.g. /dev/serial/by-id/...")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--bytesize", type=int, default=DEFAULT_BYTESIZE)
    parser.add_argument("--parity", default=DEFAULT_PARITY)
    parser.add_argument("--stopbits", type=int, default=DEFAULT_STOPBITS)
    args = parser.parse_args()

    config = SerialConfig(
        port=args.port,
        baudrate=args.baudrate,
        bytesize=args.bytesize,
        parity=args.parity,
        stopbits=args.stopbits,
        device_id=args.device_id,
    )
    return asyncio.run(_run(config))


async def _run(config: SerialConfig) -> int:
    client = MennekesModbusClient(config)
    try:
        await client.async_connect()
        identity = await async_read_identity(client)
        maximum = await client.async_read_register(MAX_EVSE_CURRENT)
    except AmtronBusError as err:
        sys.stderr.write(f"FAIL: {err}\n")
        return 1
    finally:
        await client.async_close()

    _write(f"port             {config.port}")
    _write(f"device id        {config.device_id}")
    _write(f"modbus layout    {layout_label(identity.layout_version)}")
    _write(f"firmware         {identity.firmware_version or '-'}")
    _write(f"serial number    {identity.serial_number or '-'}")
    _write(f"article number   {identity.article_number or '-'}")
    _write(f"phase options    {identity.phase_options_hw}")
    _write(f"max EVSE current {maximum}")
    return 0


def _write(line: str) -> None:
    sys.stdout.write(f"{line}\n")


if __name__ == "__main__":
    sys.exit(main())
