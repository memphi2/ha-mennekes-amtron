#!/usr/bin/env python3
"""Compile and exercise the app profile on a disposable Linux Docker runner.

The default mode loads a uniquely named test profile, creates an isolated Docker
network and virtual serial link, and removes them afterwards. Never run this on
a production Home Assistant host. --compile-only does not load kernel policy.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pty
import re
import secrets
import select
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODES = ("serial", "rtu_over_tcp", "modbus_tcp")
APP_CONNECTIONS = {
    "serial": "USB / RS-485",
    "rtu_over_tcp": "LAN: Modbus RTU (TCP)",
    "modbus_tcp": "LAN: Modbus TCP",
}


def command(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Bound subprocesses and retain useful diagnostics without shell evaluation."""
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=180, input=stdin
    )


def render_profile(source: str, name: str) -> str:
    """Mirror Supervisor's single top-level profile rename for this test only."""
    if not re.fullmatch(r"amtron-ci-[a-z0-9-]+", name):
        raise ValueError("invalid isolated profile name")
    declarations = re.findall(r"^profile ([^ ]+).*$", source, re.MULTILINE)
    if declarations != ["mennekes_amtron_modbus"]:
        raise ValueError("expected exactly the app's top-level profile")
    return re.sub(
        r"^profile mennekes_amtron_modbus ", f"profile {name} ", source,
        count=1, flags=re.MULTILINE,
    )


def compile_profile(parser: str, profile: Path) -> None:
    """Warnings are failures: an accepted but unenforced rule is not sufficient."""
    command(parser, "--skip-kernel-load", "--skip-cache", "--warn=all", "--Werror", str(profile))


@contextlib.contextmanager
def virtual_serial_link() -> Iterator[tuple[str, str]]:
    """Relay two PTYs without granting the container access to host serial ports."""
    descriptors: list[int] = []
    threads: list[threading.Thread] = []
    stopping = threading.Event()
    failures: list[BaseException] = []

    def relay(source: int, target: int) -> None:
        try:
            while not stopping.is_set():
                if not select.select([source], [], [], 0.05)[0]:
                    continue
                data = os.read(source, 4096)
                if not data:
                    return
                while data:
                    count = os.write(target, data)
                    data = data[count:]
        except OSError as error:
            if not stopping.is_set():
                failures.append(error)

    try:
        first, first_slave = pty.openpty()
        descriptors.extend((first, first_slave))
        second, second_slave = pty.openpty()
        descriptors.extend((second, second_slave))
        for source, target in ((first, second), (second, first)):
            thread = threading.Thread(target=relay, args=(source, target), daemon=True)
            thread.start()
            threads.append(thread)
        yield os.ttyname(first_slave), os.ttyname(second_slave)
        if failures:
            raise RuntimeError("virtual serial relay failed") from failures[0]
    finally:
        stopping.set()
        for thread in threads:
            thread.join(timeout=2)
        for descriptor in descriptors:
            os.close(descriptor)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("virtual serial relay did not stop")


def exercise_mode(image: str, name: str, mode: str, directory: Path) -> dict[str, object]:
    """Run the actual service and denied-access probes under the same profile."""
    data = directory / mode
    data.mkdir()
    (data / "options.json").write_text(json.dumps({
        "connection_type": APP_CONNECTIONS[mode],
        "serial_port": "/dev/serial/by-id/amtron-test",
        "baudrate": "57600",
        "frame": "8n2",
        "device_id": 50,
        "host": "wallbox",
        "tcp_port": 5020,
        "read_only": False,
        "current_cap": 16,
        "scan_interval": 2,
        "measurement_interval": 2,
        "listen": "0.0.0.0",  # noqa: S104 - isolated, unpublished test network
        "port": 8765,
    }))
    # DAC permits these accesses, so only the loaded profile can reject them.
    (data / "options.json").chmod(0o600)
    forbidden = directory / "readable-but-forbidden"
    forbidden.write_text("non-secret AppArmor test fixture\n")
    forbidden.chmod(0o644)
    container = f"{name}-{mode.replace('_', '-')}"
    with contextlib.ExitStack() as stack:
        arguments = [
            "docker", "run", "--rm", "--interactive", "--init", "--name", container,
            "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", f"--security-opt=apparmor={name}",
            "--network", name, "--network-alias", "supervisor",
            "--network-alias", "wallbox", "--volume", f"{data}:/data",
            "--volume", f"{forbidden}:/etc/amtron-apparmor-denied:ro",
        ]
        if mode == "serial":
            server, client = stack.enter_context(virtual_serial_link())
            aliases = directory / "serial-by-id"
            aliases.mkdir()
            (aliases / "amtron-test").symlink_to("../../ttyUSB99")
            # PTYs belong to their devpts instance. A bare --device recreates a
            # device node and loses that inode, so preserve it with a bind mount
            # and grant cgroup access only to these two allocated devices.
            for source, target in ((server, "/dev/ttyUSB98"), (client, "/dev/ttyUSB99")):
                device = Path(source).stat().st_rdev
                arguments.extend((
                    "--device-cgroup-rule", f"c {os.major(device)}:{os.minor(device)} rw",
                    "--volume", f"{source}:{target}",
                ))
            arguments.extend(("--volume", f"{aliases}:/dev/serial/by-id:ro"))
        arguments.extend((image, "python", "-u", "-", "--profile", name, "--mode", mode))
        try:
            result = command(*arguments, stdin=(ROOT / "scripts/apparmor_probe.py").read_text())
        finally:
            # A killed Docker client does not stop its container. Remove only
            # this invocation's unique name, including after a timeout.
            subprocess.run(
                ["docker", "rm", "--force", container], check=False,
                capture_output=True, timeout=30,
            )
    report = json.loads(result.stdout)
    if (
        not isinstance(report, dict)
        or report.get("mode") != mode
        or report.get("status") != "passed"
        or report.get("enforcing") is not True
        or report.get("profile") != name
    ):
        raise ValueError("AppArmor probe did not confirm success")
    return {str(key): value for key, value in report.items()}


def check(image: str | None, *, compile_only: bool) -> dict[str, object]:
    parser = shutil.which("apparmor_parser")
    if parser is None:
        raise RuntimeError("apparmor_parser is required; missing support is not a pass")
    name = f"amtron-ci-{secrets.token_hex(8)}"
    source = (ROOT / "modbus_service/apparmor.txt").read_text()
    with tempfile.TemporaryDirectory(prefix="amtron-apparmor-") as temporary:
        directory = Path(temporary)
        profile = directory / "profile"
        profile.write_text(render_profile(source, name))
        compile_profile(parser, profile)
        if compile_only:
            return {"status": "compiled", "enforcement_tested": False}
        if not image:
            raise ValueError("an image is required for enforcement testing")
        security = json.loads(command("docker", "info", "--format", "{{json .SecurityOptions}}").stdout)
        if not isinstance(security, list) or not any("apparmor" in item for item in security):
            raise RuntimeError("Docker must support AppArmor; cannot skip enforcement")
        privilege = [] if os.geteuid() == 0 else ["sudo", "-n"]
        with contextlib.ExitStack() as stack:
            command(*privilege, parser, "--add", "--skip-cache", str(profile))
            stack.callback(command, *privilege, parser, "--remove", "--skip-cache", str(profile))
            command("docker", "network", "create", "--internal", name)
            stack.callback(command, "docker", "network", "rm", name)
            reports = [exercise_mode(image, name, mode, directory) for mode in MODES]
            # Check the real container entrypoint and externally delivered
            # Docker SIGTERM as well as the in-container lifecycle probe.
            from service_image import smoke

            smoke(image, apparmor_profile=name)
    return {"status": "passed", "enforcement_tested": True, "transports": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args()
    if not args.compile_only and not args.image:
        parser.error("--image is required unless --compile-only is selected")
    try:
        result = check(args.image, compile_only=args.compile_only)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.stderr.write(f"AppArmor validation failed: {type(error).__name__}: {error}\n")
        if isinstance(error, subprocess.CalledProcessError):
            sys.stderr.write((error.stdout or "")[-8000:] + (error.stderr or "")[-8000:])
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
