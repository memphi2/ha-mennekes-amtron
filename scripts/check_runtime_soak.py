"""Measure bounded HA lifecycle resources in an isolated, offline process."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {"integration_bytes": 512 * 1024, "traced_bytes": 8 * 1024**2,
          "rss_bytes": 32 * 1024**2, "tasks": 2, "timers": 4, "fds": 2,
          "listeners": 0}
PHASES = ("loaded", "recovered", "idle", "unloaded")


def sample_errors(samples: list[dict[str, Any]], *, phases: tuple[str, ...] = PHASES) -> list[str]:
    """Compare equivalent phases after warmup and reject retained resources."""
    errors = []
    for phase in phases:
        group = [sample for sample in samples if sample.get("phase") == phase]
        if len(group) < 3:
            errors.append(f"{phase}: insufficient samples")
            continue
        for metric, limit in LIMITS.items():
            values: list[Any] = [sample.get(metric) for sample in group]
            if any(type(value) is not int or value < 0 for value in values):
                errors.append(f"{phase}: invalid {metric}")
            elif max(values) - values[0] > limit:
                errors.append(f"{phase}: {metric} growth exceeds {limit}")
        if any(sample.get("retained_clients") != 0 for sample in group):
            errors.append(f"{phase}: old clients retained")
        if phase == "unloaded" and any(
            sample.get(metric) != 0 for sample in group
            for metric in ("owned_tasks", "listeners", "open_clients")
        ):
            errors.append("unloaded: tasks, listeners or clients survived cleanup")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=30)
    parser.add_argument("--idle-seconds", type=float, default=10)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.cycles < 3 or not math.isfinite(args.idle_seconds) or args.idle_seconds < 0.1:
        parser.error("use at least three cycles and 0.1 finite idle seconds")
    with tempfile.TemporaryDirectory(prefix="amtron-soak-") as directory:
        report = Path(directory) / "report.json"
        env = dict(os.environ, AMTRON_SOAK_CYCLES=str(args.cycles),
                   AMTRON_SOAK_IDLE=str(args.idle_seconds), AMTRON_SOAK_REPORT=str(report))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_runtime_soak.py"],
            cwd=ROOT, env=env, check=False,
        )
        if result.returncode:
            return result.returncode
        if not report.exists():
            sys.stderr.write("Soak failed: missing measurement report\n")
            return 1
        data = json.loads(report.read_text())
        errors = sample_errors(data.get("samples", []))
        if data.get("cycles") != args.cycles or data.get("idle_seconds") != args.idle_seconds:
            errors.append("measurement configuration does not match requested run")
        if data.get("serial_cycles") != args.cycles or data.get("serial_fd_growth") != 0:
            errors.append("serial open/close verification incomplete")
        if errors:
            sys.stderr.write("\n".join(errors) + "\n")
            return 1
        if args.json_output:
            args.json_output.write_text(json.dumps(data, indent=2) + "\n")
        sys.stdout.write(f"Soak passed: {args.cycles} lifecycle and serial cycles; {args.idle_seconds}s idle\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
