"""Exercise the installed service inside its enforcing AppArmor container.

Feed this file to ``python -u - --profile NAME --mode TRANSPORT``. The caller
provides isolated DNS aliases, virtual serial devices and writable /data.
No Home Assistant, host device or production credential is involved.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import logging
import os
import stat
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from amtron_device import registers
from amtron_device.decode import encode_register
from pymodbus import FramerType
from pymodbus.server import ModbusSerialServer, ModbusTcpServer
from pymodbus.simulator import SimData, SimDevice
from pymodbus.simulator.simutils import DataType

SERIAL = "APPARMOR-PROBE"
SUPERVISOR_TOKEN = "isolated-supervisor-probe-credential"  # noqa: S105 - isolated fixture
DATA = Path("/data")
API = "http://127.0.0.1:8765"


class ProbeError(Exception):
    """A bounded, credential-free explanation of an unsuccessful check."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise ProbeError(message)


def register_words() -> list[int]:
    """Build the register layout required by the real installed service."""
    words = [0] * 0x1010
    for spec, value in (
        (registers.MODBUS_LAYOUT_VERSION, registers.LAYOUT_V01_03),
        (registers.EVSE_STATE, 1), (registers.AUTHORIZATION_STATUS, 1),
        (registers.DOWNGRADE_STATUS, 1), (registers.CP_STATE, 11),
        (registers.CHARGING_RELEASE, 1), (registers.EMS_FALLBACK_CURRENT, 6),
        (registers.PHASE_OPTIONS_HW, 2), (registers.MAX_EVSE_CURRENT, 16),
        (registers.MAX_CURRENT_HOUSE, 32), (registers.CHARGING_CURRENT_EMS, 8),
        (registers.DOWNGRADE_CURRENT, 8),
    ):
        encoded = encode_register(spec, value)
        words[spec.address:spec.address + len(encoded)] = encoded
    for spec, value in (
        (registers.FIRMWARE_VERSION, "2023.21.11024"),
        (registers.SERIAL_NUMBER, SERIAL),
        (registers.ARTICLE_NUMBER, "1313201205"),
    ):
        raw = value.encode().ljust(spec.count * 2, b"\0")
        for index in range(spec.count):
            words[spec.address + index] = int.from_bytes(raw[index * 2:index * 2 + 2])
    return [word - 0x10000 if word > 0x7FFF else word for word in words]


class Child:
    """Run the actual entrypoint and retain a bounded log for assertions only."""

    def __init__(self, profile: str) -> None:
        self.profile = profile
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.output = bytearray()
        self.overflow = False

    async def start(self) -> None:
        code = (
            "from pathlib import Path; "
            f"assert Path('/proc/self/attr/current').read_text().strip() == {self.profile + ' (enforce)'!r}; "
            "from amtron_device import service_discovery, server; "
            "service_discovery.SUPERVISOR_URL = 'http://supervisor:8766'; "
            "server.main()"
        )
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", code,
            env=dict(os.environ, SUPERVISOR_TOKEN=SUPERVISOR_TOKEN),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        require(self.process is not None and self.process.stdout is not None, "child output missing")
        if self.process is None or self.process.stdout is None:
            return
        while chunk := await self.process.stdout.read(4096):
            if len(self.output) + len(chunk) > 131072:
                self.overflow = True
            self.output.extend(chunk)
            del self.output[:-131072]

    async def stop(self, *, clean: bool = True) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), 15)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
            if clean:
                raise ProbeError("service did not stop gracefully") from None
        finally:
            if self.reader is not None:
                await self.reader
        if clean:
            require(self.process.returncode == 0, "service exited unsuccessfully")
            require(not self.overflow, "service emitted excessive output")
            require(b"SIGTERM received" in self.output, "shutdown signal was not handled")
            require(b"Modbus service shutdown complete" in self.output, "shutdown was incomplete")


def denied_files() -> None:
    """Require policy denials, not missing files or ordinary filesystem errors."""
    fixture = Path("/etc/amtron-apparmor-denied")
    require(stat.S_ISREG(fixture.stat().st_mode), "negative read fixture is not a file")
    require(fixture.stat().st_mode & stat.S_IROTH, "negative read fixture is not world-readable")
    options = DATA / "options.json"
    require(options.stat().st_uid == os.geteuid(), "options ownership cannot prove policy denial")
    require(options.stat().st_mode & stat.S_IWUSR, "options DAC is not writable")
    require(DATA.stat().st_uid == os.geteuid(), "data ownership cannot prove policy denial")
    require(DATA.stat().st_mode & stat.S_IWUSR, "data DAC is not writable")
    checks: tuple[tuple[str, Callable[[], object]], ...] = (
        ("protected read", fixture.read_bytes),
        ("options write", lambda: os.open(options, os.O_WRONLY)),
        ("unapproved state write", lambda: os.open(DATA / "not-permitted", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)),
    )
    for name, operation in checks:
        try:
            result = operation()
        except OSError as error:
            require(error.errno in (errno.EACCES, errno.EPERM), f"{name} failed without a policy denial")
        else:
            if isinstance(result, int):
                os.close(result)
            raise ProbeError(f"{name} was allowed")
    require(Path("/bin/sh").stat().st_mode & stat.S_IXOTH, "negative executable fixture is not executable")


async def denied_access() -> None:
    denied_files()
    try:
        shell = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", "exit 0", stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as error:
        require(error.errno in (errno.EACCES, errno.EPERM), "shell failed without a policy denial")
    else:
        await shell.wait()
        raise ProbeError("shell execution was allowed")


def check_environment(profile: str) -> None:
    require(Path("/proc/self/attr/current").read_text().strip() == f"{profile} (enforce)", "probe is not enforcing the expected profile")
    require(not (DATA / "service-token").exists(), "token fixture must start empty")
    require(not (DATA / "intent.json").exists(), "intent fixture must start empty")


def check_temporary_files() -> None:
    require(not list(DATA.glob(".token-*")) and not list(DATA.glob(".intent-*")), "atomic save left temporary files")


async def probe(profile: str, mode: str) -> dict[str, object]:
    check_environment(profile)
    writes: list[tuple[int, list[int]]] = []
    discoveries: list[str] = []

    async def action(_function: int, _start: int, address: int, _count: int,
                     _registers: list[int], values: list[int] | list[bool] | None) -> None:
        if values is not None:
            require(len(writes) < 128, "unexpectedly many Modbus writes")
            writes.append((address, [int(value) for value in values]))

    device = SimDevice(50, simdata=[SimData(0, values=register_words(), datatype=DataType.REGISTERS)], action=action)
    wallbox: ModbusSerialServer | ModbusTcpServer
    if mode == "serial":
        wallbox = ModbusSerialServer([device], framer=FramerType.RTU, port="/dev/ttyUSB98",
                                    baudrate=57600, bytesize=8, parity="N", stopbits=2)
    else:
        wallbox = ModbusTcpServer([device], framer=FramerType.RTU if mode == "rtu_over_tcp" else FramerType.SOCKET,
                                 address=("0.0.0.0", 5020))  # noqa: S104 - isolated probe network

    async def supervisor(request: web.Request) -> web.Response:
        require(request.headers.get("Authorization") == f"Bearer {SUPERVISOR_TOKEN}", "Supervisor authentication differs")
        if request.path == "/addons/self/info":
            return web.json_response({"result": "ok", "data": {"hostname": "local-mennekes-amtron-modbus"}})
        payload = await request.json()
        require(payload["service"] == "mennekes_amtron_rtu", "discovery service differs")
        require(payload["config"]["port"] == 8765, "discovery API port differs")
        token = payload["config"]["token"]
        require(isinstance(token, str) and len(token) >= 32, "discovery token is invalid")
        discoveries.append(token)
        return web.json_response({"result": "ok", "data": {}})

    app = web.Application()
    app.router.add_get("/addons/self/info", supervisor)
    app.router.add_post("/discovery", supervisor)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    child = Child(profile)
    children = [child]
    token = ""
    try:
        await web.TCPSite(runner, "0.0.0.0", 8766).start()  # noqa: S104 - isolated probe network
        await wallbox.serve_forever(background=True)
        async with ClientSession(timeout=ClientTimeout(total=3), trust_env=False) as session:
            async def ready() -> dict[str, Any]:
                nonlocal token
                for _ in range(150):
                    require(child.process is not None and child.process.returncode is None, "service stopped during initialization")
                    try:
                        token = (DATA / "service-token").read_text()
                        async with session.get(API + "/v2/state", headers={"Authorization": f"Bearer {token}"}) as response:
                            payload = await response.json()
                            if response.status == 200 and payload.get("available") and payload["heartbeat"]["healthy"] is True and len(discoveries) >= len(children):
                                require(payload["identity"]["serial_number"] == SERIAL, "initialized identity differs")
                                return dict(payload)
                    except (ClientError, TimeoutError, FileNotFoundError):
                        pass
                    await asyncio.sleep(0.1)
                raise ProbeError("service did not become ready and discoverable")

            await child.start()
            await ready()
            require(discoveries == [token], "discovery token differs from persistent token")
            require(stat.S_IMODE((DATA / "service-token").stat().st_mode) == 0o600, "token file is not private")
            await asyncio.sleep(5.1)
            async with session.post(API + "/v2/command", headers={"Authorization": f"Bearer {token}"},
                                    json={"command": "pause", "expected_serial_number": SERIAL}) as response:
                payload = await response.json()
                require(response.status == 200, "pause command was rejected")
                require(payload["control"]["pause_requested"] is True, "pause was not retained")
                require(payload["values"]["charging_current_ems"] == 1, "pause was not written")
            heartbeat_before = sum(address == registers.HEARTBEAT.address for address, _values in writes)
            controls_before = [(address, values) for address, values in writes if address != registers.HEARTBEAT.address]
            require(len(controls_before) == 1 and controls_before[0][0] == registers.CHARGING_CURRENT_EMS.address, "unexpected charging writes")
            await asyncio.sleep(5.5)
            require(sum(address == registers.HEARTBEAT.address for address, _values in writes) > heartbeat_before, "heartbeat did not continue while paused")
            saved = json.loads((DATA / "intent.json").read_text())
            require(saved["pause_requested"] is True, "pause was not persisted")
            await child.stop()
            original_token = token
            child = Child(profile)
            children.append(child)
            await child.start()
            restored = await ready()
            require(token == original_token and discoveries == [token, token], "restart rotated credentials")
            require(restored["control"]["pause_requested"] is True, "restart lost pause intent")
            require(restored["values"]["charging_current_ems"] == 1, "restart resumed charging")
            require([(address, values) for address, values in writes if address != registers.HEARTBEAT.address] == controls_before, "restart replayed a charging command")
            await denied_access()
            await child.stop()
            check_temporary_files()
            for instance in children:
                require(token.encode() not in instance.output and SUPERVISOR_TOKEN.encode() not in instance.output, "service logs exposed a credential")
                require(b"Heartbeat started" in instance.output and b"Heartbeat stopped" in instance.output, "heartbeat lifecycle logging is incomplete")
        return {"status": "passed", "mode": mode, "profile": profile, "enforcing": True,
                "discovery": True, "heartbeat": True, "pause_restart": True,
                "token_preserved": True, "denials": 4, "shutdown": True}
    finally:
        for instance in children:
            await instance.stop(clean=False)
        await wallbox.shutdown()  # type: ignore[no-untyped-call]  # pymodbus lifecycle API lacks annotations
        await runner.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--mode", choices=("serial", "rtu_over_tcp", "modbus_tcp"), required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(asyncio.wait_for(probe(args.profile, args.mode), 90))
    except Exception as error:  # noqa: BLE001 - never expose captured credentials or raw exceptions
        detail = str(error) if isinstance(error, ProbeError) else type(error).__name__
        sys.stderr.write(f"AppArmor probe failed: {detail[:240]}\n")
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
