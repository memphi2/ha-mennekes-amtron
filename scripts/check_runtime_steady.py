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
from typing import Any

from check_runtime_soak import ROOT, sample_errors


def measurement_errors(data: dict[str, Any], seconds: float) -> list[str]:
    """Check complete measurements without losing evidence on malformed reports."""
    samples = data.get("samples", [])
    if not isinstance(samples, list) or any(not isinstance(sample, dict) for sample in samples):
        errors = ["invalid resource samples"]
    else:
        errors = sample_errors(samples, phases=("steady",))

    def number(key: str) -> float:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        return -1.0

    if data.get("status") != "passed":
        errors.append("runtime test did not complete successfully")
    if number("seconds") != seconds or number("elapsed") < seconds:
        errors.append("duration does not match the requested run")
    if number("heartbeats") < seconds / 10 or number("recoveries") < 1:
        errors.append("heartbeat or recovery was not exercised")
    if not 0 <= number("heartbeat_max_gap") <= 10:
        errors.append("heartbeat delivery gap exceeded ten seconds or was not measured")
    if data.get("transport") != "serial_loopback" or number("measurement_changes") < 2:
        errors.append("real serial reconnects and changing measurements were not exercised")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3600)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 10 <= args.seconds <= 7200:
        parser.error("duration must be between 10 and 7200 seconds")
    with tempfile.TemporaryDirectory(prefix="amtron-steady-") as temporary:
        # Checkpoints must already be at the artifact path if CI kills this
        # wrapper too. Replace any prior run before starting the subprocess.
        report = args.json_output.resolve() if args.json_output else Path(temporary) / "report.json"
        report.write_text(json.dumps({"status": "running", "stage": "starting",
                                      "seconds": args.seconds, "samples": []}) + "\n")
        env = dict(os.environ, AMTRON_STEADY_SECONDS=str(args.seconds), AMTRON_STEADY_REPORT=str(report))
        failure_type = None
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "tests/test_runtime_steady.py"],
                cwd=ROOT, env=env, check=False, timeout=args.seconds + 120,
            )
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            returncode, failure_type = 124, "timeout"
        except OSError:
            returncode, failure_type = 1, "process_start_failed"
        try:
            data = json.loads(report.read_text())
            if not isinstance(data, dict):
                raise ValueError("report must be an object")
        except (OSError, ValueError, UnicodeError):
            data = {"seconds": args.seconds, "status": "failed", "samples": []}
            failure_type = failure_type or "missing_or_invalid_report"
        errors = measurement_errors(data, args.seconds)
        if returncode:
            errors.insert(0, f"runtime test exited with status {returncode}")
            data.setdefault("failure_type", "test_failure")
        if failure_type:
            data["failure_type"] = failure_type
        data.update(status="failed" if errors else "passed",
                    test_returncode=returncode, validation_errors=errors)
        if args.json_output:
            args.json_output.write_text(json.dumps(data, indent=2) + "\n")
        if errors:
            sys.stderr.write("\n".join(errors) + "\n")
            return returncode if returncode > 0 else 1
        sys.stdout.write(f"Steady runtime passed: {args.seconds}s continuously loaded\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
