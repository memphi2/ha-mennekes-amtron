#!/usr/bin/env python3
"""Run the current Python coverage gate.

This is the local Home Assistant Platinum coverage ratchet.
"""

from __future__ import annotations

import subprocess
import sys

# Above the Platinum target of 95. The suite leaves no statement uncovered, so
# the ratchet sits just under the measured value and catches a real regression
# instead of tolerating one.
MINIMUM_COVERAGE = 99


def main() -> int:
    """Run pytest with the coverage ratchet."""

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=custom_components/mennekes_amtron",
            "--cov-report=term-missing:skip-covered",
            f"--cov-fail-under={MINIMUM_COVERAGE}",
            "-q",
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
