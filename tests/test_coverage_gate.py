"""Coverage gates must use exact evidence and reject missing or incomplete data."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import check_coverage as coverage

MODULE = f"{coverage.COMPONENT}/sensor.py"
FLOW = f"{coverage.COMPONENT}/config_flow.py"


def measured(lines=100, covered_lines=100, branches=0, covered_branches=0):
    return {"summary": {
        "num_statements": lines, "covered_lines": covered_lines,
        "num_branches": branches, "covered_branches": covered_branches,
        # Deliberately misleading: the gate must calculate from counters.
        "percent_covered": 100,
    }}


def test_per_module_threshold_cannot_hide_behind_high_global_coverage():
    other = f"{coverage.COMPONENT}/other.py"
    report = {"files": {MODULE: measured(100, 95), other: measured(10000, 10000)}}
    failures = coverage.validate_report(report, modules={MODULE, other})
    assert failures == [f"{MODULE}: coverage 95.00% must exceed 95%"]
    report["files"][MODULE] = measured(100, 96)
    assert coverage.validate_report(report, modules={MODULE, other}) == []


@pytest.mark.parametrize("file", coverage.FLOW_MODULES)
def test_flow_coverage_requires_every_statement_and_branch(file):
    module = f"{coverage.COMPONENT}/{file}"
    report = {"files": {module: measured(50, 50, 50, 49)}}
    failures = coverage.validate_report(report, modules={module})
    assert failures == [f"{module}: config-flow coverage must be 100%; 1 statements/branches remain uncovered"]


def test_global_threshold_uses_exact_counts_before_rounding():
    report = {"files": {MODULE: measured(100000, 98999)}}
    assert any("Global coverage" in item for item in coverage.validate_report(report, modules={MODULE}))
    report["files"][MODULE] = measured(100000, 99000)
    assert coverage.validate_report(report, modules={MODULE}) == []


@pytest.mark.parametrize("value", [
    None, {}, {"summary": {}},
    measured(1, 2), measured(1, 1, 1, 2), measured(True, 1),
    measured(1, 1, -1, 0), measured(100, 99.0),
])
def test_missing_or_invalid_module_data_never_passes(value):
    assert coverage.validate_report({"files": {MODULE: value}}, modules={MODULE})


def test_empty_modules_and_absolute_report_paths_are_supported():
    report = {"files": {str(coverage.ROOT / MODULE): measured(0, 0)}}
    assert coverage.validate_report(report, modules={MODULE}) == []


@pytest.mark.parametrize("report", [None, [], {}, {"files": []}])
def test_report_requires_a_mapping_of_files(report):
    assert coverage.validate_report(report, modules={MODULE})


def test_every_current_source_module_is_required():
    assert any("missing module summary" in item for item in coverage.validate_report({"files": {}}))


def test_missing_source_directory_does_not_turn_empty_coverage_into_success(monkeypatch, tmp_path):
    monkeypatch.setattr(coverage, "ROOT", tmp_path)
    assert coverage.validate_report({"files": {}}) == [
        "Coverage source inventory is empty; no integration modules were found"
    ]


@pytest.mark.parametrize("outcome", ["pass", "tests_fail", "missing", "invalid", "uncovered"])
def test_command_validates_only_a_successful_fresh_test_report(monkeypatch, outcome):
    validated = []
    monkeypatch.setattr(coverage, "validate_report", lambda report: validated.append(report) or (
        ["uncovered source"] if outcome == "uncovered" else []
    ))

    def run(command, **kwargs):
        assert kwargs["cwd"] == coverage.ROOT
        report_arg = next(arg for arg in command if arg.startswith("--cov-report=json:"))
        path = Path(report_arg.removeprefix("--cov-report=json:"))
        if outcome not in {"missing", "tests_fail"}:
            path.write_text("broken" if outcome == "invalid" else json.dumps({"files": {}}))
        return subprocess.CompletedProcess(command, 7 if outcome == "tests_fail" else 0)

    monkeypatch.setattr(coverage.subprocess, "run", run)
    result = coverage.main()
    assert result == (0 if outcome == "pass" else 7 if outcome == "tests_fail" else 1)
    assert bool(validated) == (outcome in {"pass", "uncovered"})
