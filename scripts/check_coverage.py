#!/usr/bin/env python3
"""Measure global, per-module and complete config-flow coverage for this source."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from check_reporting import report_failures

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = "custom_components/mennekes_amtron_rtu"
MINIMUM_COVERAGE = 99
MINIMUM_MODULE_COVERAGE = 95
FLOW_MODULES = frozenset({
    "config_flow.py", "options_flow.py", "config_schemas.py",
    "_flow_connect.py", "_flow_search.py", "_flow_serial.py",
    "_flow_gateway.py",
    "_options_base.py", "_options_draft.py", "_options_estimates.py",
    "network_tariff_config.py",
})
COUNTERS = ("num_statements", "covered_lines", "num_branches", "covered_branches")


def _coverage_counts(value: Any) -> tuple[int, int]:
    """Use exact statement/branch counts, never rounded percentage strings."""
    if not isinstance(value, dict) or not isinstance(value.get("summary"), dict):
        raise ValueError("missing module summary")
    summary = value["summary"]
    if any(type(summary.get(key)) is not int or summary[key] < 0 for key in COUNTERS):
        raise ValueError("missing or invalid statement/branch counters")
    lines, covered_lines, branches, covered_branches = (summary[key] for key in COUNTERS)
    if covered_lines > lines or covered_branches > branches:
        raise ValueError("covered counts exceed executable counts")
    return covered_lines + covered_branches, lines + branches


def validate_report(report: object, *, modules: set[str] | None = None) -> list[str]:
    """Require every source module, >95% per module and 100% config-flow coverage."""
    if not isinstance(report, dict) or not isinstance(report.get("files"), dict):
        return ["Coverage JSON has no files mapping"]
    expected = modules if modules is not None else {
        str(path.relative_to(ROOT)) for path in (ROOT / COMPONENT).rglob("*.py")
    }
    if not expected:
        return ["Coverage source inventory is empty; no integration modules were found"]
    files = report["files"]
    failures: list[str] = []
    covered_total = executable_total = valid_modules = 0
    for module in sorted(expected):
        value = files.get(module, files.get(str(ROOT / module)))
        try:
            covered, executable = _coverage_counts(value)
        except ValueError as error:
            failures.append(f"{module}: {error}")
            continue
        valid_modules += 1
        covered_total += covered
        executable_total += executable
        is_flow = module in {f"{COMPONENT}/{name}" for name in FLOW_MODULES}
        if is_flow and covered != executable:
            failures.append(
                f"{module}: config-flow coverage must be 100%; "
                f"{executable - covered} statements/branches remain uncovered"
            )
        elif executable and covered * 100 <= executable * MINIMUM_MODULE_COVERAGE:
            failures.append(
                f"{module}: coverage {covered / executable:.2%} must exceed "
                f"{MINIMUM_MODULE_COVERAGE}%"
            )
    if valid_modules == len(expected) and covered_total * 100 < executable_total * MINIMUM_COVERAGE:
        failures.append(
            f"Global coverage {covered_total / executable_total:.2%} is below "
            f"{MINIMUM_COVERAGE}%"
        )
    return failures


def main() -> int:
    """Run the tests once and validate their actual machine-readable report."""
    with tempfile.TemporaryDirectory(prefix="amtron-coverage-") as temporary:
        report_path = Path(temporary) / "coverage.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", f"--cov={COMPONENT}",
                "--cov-report=term-missing:skip-covered",
                f"--cov-report=json:{report_path}",
                f"--cov-fail-under={MINIMUM_COVERAGE}", "-q",
            ],
            cwd=ROOT,
            check=False,
        )
        if result.returncode:
            return result.returncode
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            return report_failures([f"Cannot validate coverage report: {error}"], "")
        return report_failures(validate_report(report), "Module and config-flow coverage passed")


if __name__ == "__main__":
    sys.exit(main())
