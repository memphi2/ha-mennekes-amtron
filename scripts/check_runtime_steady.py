"""Measure continuously loaded HA runtime resources with real elapsed time."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from check_runtime_soak import ROOT, sample_errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3600)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 10 <= args.seconds <= 7200:
        parser.error("duration must be between 10 and 7200 seconds")
    with tempfile.TemporaryDirectory(prefix="amtron-steady-") as temporary:
        report = Path(temporary) / "report.json"
        env = dict(os.environ, AMTRON_STEADY_SECONDS=str(args.seconds), AMTRON_STEADY_REPORT=str(report))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_runtime_steady.py"],
            cwd=ROOT, env=env, check=False, timeout=args.seconds + 120,
        )
        if result.returncode:
            return result.returncode
        if not report.exists():
            sys.stderr.write("Steady runtime test did not produce a report\n")
            return 1
        data = json.loads(report.read_text())
        errors = sample_errors(data.get("samples", []), phases=("steady",))
        if data.get("seconds") != args.seconds or data.get("elapsed", 0) < args.seconds:
            errors.append("duration does not match the requested run")
        if data.get("heartbeats", 0) < args.seconds / 10 or data.get("recoveries", 0) < 1:
            errors.append("heartbeat or recovery was not exercised")
        gap = data.get("heartbeat_max_gap")
        if type(gap) not in (int, float) or not math.isfinite(gap) or not 0 <= gap <= 10:
            errors.append("heartbeat delivery gap exceeded ten seconds or was not measured")
        if data.get("transport") != "serial_loopback" or data.get("measurement_changes", 0) < 2:
            errors.append("real serial reconnects and changing measurements were not exercised")
        if errors:
            sys.stderr.write("\n".join(errors) + "\n")
            return 1
        if args.json_output:
            args.json_output.write_text(json.dumps(data, indent=2) + "\n")
        sys.stdout.write(f"Steady runtime passed: {args.seconds}s continuously loaded\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
