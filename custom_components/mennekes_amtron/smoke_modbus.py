#!/usr/bin/env python3
"""Read-only bus check for an AMTRON wallbox.

This file ships with the integration so it is already on the machine the
USB RS-485 adapter is plugged into. After installing the integration it is at
``/config/custom_components/mennekes_amtron/smoke_modbus.py``, and Home
Assistant's own Python already has pymodbus, so nothing has to be installed.

    # Home Assistant OS, Supervised or Container
    docker exec -it homeassistant \
        python /config/custom_components/mennekes_amtron/smoke_modbus.py \
        /dev/serial/by-id/<your-adapter>

    # Home Assistant Core in a virtual environment
    /srv/homeassistant/bin/python \
        /config/custom_components/mennekes_amtron/smoke_modbus.py \
        /dev/serial/by-id/<your-adapter>

It never writes. It cannot put the wallbox into the energy-manager error
state, and it proves the wiring, the bus parameters and the device address on
their own, before the integration is configured.

``--scan`` tries every documented device address, and with ``--scan-baudrate``
every documented baud rate as well, for a wallbox whose configuration is
unknown.

The file deliberately imports nothing from the integration: it has to run as a
plain script, from any directory, on a machine that has no copy of this
repository.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

try:
    from pymodbus import FramerType
    from pymodbus.client import AsyncModbusSerialClient
    from pymodbus.client.mixin import ModbusClientMixin
except ImportError:  # pragma: no cover - only hit outside Home Assistant
    sys.stderr.write(
        "pymodbus is missing. Run this with the Python that Home Assistant "
        "itself uses, for example:\n"
        "  docker exec -it homeassistant python "
        "/config/custom_components/mennekes_amtron/smoke_modbus.py <port>\n"
    )
    raise SystemExit(2) from None

DEFAULT_BAUDRATE = 57600
DEFAULT_DEVICE_ID = 50
DEVICE_ID_RANGE = range(10, 51)
BAUDRATES = (57600, 38400, 28800, 19200, 14400, 9600, 56000)

MODBUS_LAYOUT_VERSION = 0x0000
FIRMWARE_VERSION = 0x0001
SERIAL_NUMBER = 0x0013
ARTICLE_NUMBER = 0x001B
EVSE_STATE = 0x0100
MAX_EVSE_CURRENT = 0x0306
STRING_REGISTERS = 8

EVSE_STATES = {
    0: "not initialized",
    1: "idle, no vehicle connected",
    2: "vehicle connected",
    3: "preconditions valid, not charging yet",
    4: "ready to charge",
    5: "charging",
    6: "error",
    7: "service mode",
}


def main(argv: list[str] | None = None) -> int:
    """Parse the bus parameters and run the read-only check."""

    parser = argparse.ArgumentParser(
        description="Read-only Modbus check for a MENNEKES AMTRON wallbox.",
    )
    parser.add_argument("port", help="Serial port, e.g. /dev/serial/by-id/...")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--bytesize", type=int, default=8)
    parser.add_argument("--parity", default="N", choices=("N", "E", "O"))
    parser.add_argument("--stopbits", type=int, default=2, choices=(1, 2))
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds to wait for an answer. Defaults to 1, or 0.5 while scanning.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Try every documented device address from 10 to 50.",
    )
    parser.add_argument(
        "--scan-baudrate",
        action="store_true",
        help="With --scan, try every documented baud rate as well.",
    )
    args = parser.parse_args(argv)
    if args.timeout is None:
        # A scan can make a few hundred attempts, so it waits less for each.
        args.timeout = 0.5 if args.scan else 1.0
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    if args.scan:
        return await _scan(args)
    return await _probe_once(args, args.baudrate, args.device_id, verbose=True)


async def _scan(args: argparse.Namespace) -> int:
    baudrates = BAUDRATES if args.scan_baudrate else (args.baudrate,)
    attempts = len(baudrates) * len(DEVICE_ID_RANGE)
    _write(f"up to {attempts} attempts, about {attempts * args.timeout:.0f} s\n")
    for baudrate in baudrates:
        _write(f"scanning at {baudrate} baud, {args.bytesize}{args.parity}{args.stopbits}")
        for device_id in DEVICE_ID_RANGE:
            if await _probe_once(args, baudrate, device_id, verbose=False) == 0:
                _write(f"\nfound a wallbox at device address {device_id}, {baudrate} baud")
                return await _probe_once(args, baudrate, device_id, verbose=True)
    _write("\nno wallbox answered.")
    _write("Check the wiring (Modbus A is +, B is -, GND connected), and that")
    _write("DIP 4 and DIP 5 on bank S1 are on and the wallbox was restarted.")
    return 1


async def _probe_once(
    args: argparse.Namespace,
    baudrate: int,
    device_id: int,
    *,
    verbose: bool,
) -> int:
    client = AsyncModbusSerialClient(
        args.port,
        framer=FramerType.RTU,
        baudrate=baudrate,
        bytesize=args.bytesize,
        parity=args.parity,
        stopbits=args.stopbits,
        timeout=args.timeout,
        retries=1 if args.scan else 2,
    )
    try:
        if not await client.connect():
            if verbose:
                _fail(f"cannot open {args.port}")
            return 1
        layout = await _read_uint16(client, MODBUS_LAYOUT_VERSION, device_id)
        if layout is None or layout <= 0:
            if verbose:
                _fail(
                    "the device did not report a Modbus layout version. Check "
                    "the device address and whether DIP 4 and DIP 5 on bank S1 "
                    "are on."
                )
            return 1
        if not verbose:
            return 0

        _write(f"port             {args.port}")
        _write(f"bus              {baudrate} baud, "
               f"{args.bytesize}{args.parity}{args.stopbits}")
        _write(f"device address   {device_id}")
        _write(f"modbus layout    v{layout >> 8:02d}.{layout & 0xFF:02d}")
        _write(f"firmware         "
               f"{await _read_text(client, FIRMWARE_VERSION, device_id) or '-'}")
        _write(f"serial number    "
               f"{await _read_text(client, SERIAL_NUMBER, device_id) or '-'}")
        _write(f"article number   "
               f"{await _read_text(client, ARTICLE_NUMBER, device_id) or '-'}")
        state = await _read_uint16(client, EVSE_STATE, device_id)
        if state is not None:
            _write(f"evse state       {state} ({EVSE_STATES.get(state, 'unknown')})")
        maximum = await _read_float32(client, MAX_EVSE_CURRENT, device_id)
        if maximum is not None:
            _write(f"max EVSE current {maximum:.1f} A")
        _write("\nThe bus is fine. Add the integration with these values.")
        return 0
    except Exception as err:  # noqa: BLE001 - a probe reports, it does not raise
        if verbose:
            _fail(str(err) or err.__class__.__name__)
        return 1
    finally:
        client.close()


async def _read(client: object, address: int, count: int, device_id: int) -> list[int] | None:
    response = await client.read_holding_registers(  # type: ignore[attr-defined]
        address, count=count, device_id=device_id
    )
    if response is None or response.isError():
        return None
    registers = getattr(response, "registers", [])
    return [int(word) for word in registers] if len(registers) == count else None


async def _read_uint16(client: object, address: int, device_id: int) -> int | None:
    words = await _read(client, address, 1, device_id)
    return words[0] if words else None


async def _read_float32(client: object, address: int, device_id: int) -> float | None:
    words = await _read(client, address, 2, device_id)
    if not words:
        return None
    value = ModbusClientMixin.convert_from_registers(
        words, ModbusClientMixin.DATATYPE.FLOAT32
    )
    return float(value) if isinstance(value, (int, float)) else None


async def _read_text(client: object, address: int, device_id: int) -> str | None:
    words = await _read(client, address, STRING_REGISTERS, device_id)
    if not words:
        return None
    value = ModbusClientMixin.convert_from_registers(
        words, ModbusClientMixin.DATATYPE.STRING
    )
    text = str(value).split("\x00", 1)[0].strip()
    cleaned = "".join(char for char in text if char.isprintable())
    return cleaned or None


def _write(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def _fail(message: str) -> None:
    sys.stderr.write(f"FAIL: {message}\n")


if __name__ == "__main__":
    sys.exit(main())
