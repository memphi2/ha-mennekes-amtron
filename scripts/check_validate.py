#!/usr/bin/env python3
"""Run the same validation gates as the main CI job."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ValidationStep:
    """One ordered validation command."""

    name: str
    command: tuple[str, ...]
    cwd: Path = ROOT


def steps() -> list[ValidationStep]:
    """Return the validation sequence, in the order CI runs it."""

    return [
        ValidationStep("Repository checks", (sys.executable, "scripts/check_repo.py")),
        ValidationStep(
            "Legal/provenance audit",
            (sys.executable, "scripts/check_legal_audit.py"),
        ),
        ValidationStep(
            "Quality scale checks",
            (sys.executable, "scripts/check_quality_scale.py"),
        ),
        ValidationStep(
            "Register map checks",
            (sys.executable, "scripts/check_register_map.py"),
        ),
        ValidationStep(
            "pymodbus pin check",
            (sys.executable, "scripts/check_pymodbus_pin.py"),
        ),
        ValidationStep("Ruff", (sys.executable, "-m", "ruff", "check", ".")),
        ValidationStep("Python tests", (sys.executable, "-m", "pytest")),
        ValidationStep(
            "Python coverage ratchet",
            (sys.executable, "scripts/check_coverage.py"),
        ),
        ValidationStep(
            "Python typing ratchet",
            (sys.executable, "scripts/check_typing.py"),
        ),
    ]


def main() -> int:
    """Run the full local validation sequence."""

    for step in steps():
        returncode = _run_step(step)
        if returncode:
            return returncode
    _write("Validation passed")
    return 0


def _run_step(step: ValidationStep) -> int:
    _write(f"\n==> {step.name}")
    _write(f"$ {_format_command(step.command)}")
    return subprocess.run(step.command, cwd=step.cwd, check=False).returncode


def _format_command(command: Sequence[str]) -> str:
    return " ".join(command)


def _write(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
