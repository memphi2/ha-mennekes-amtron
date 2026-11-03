"""Continuous-soak failures must leave useful evidence and still fail CI."""

import importlib
import json
import subprocess
import tracemalloc
from pathlib import Path

import pytest

from scripts.check_runtime_soak import LIMITS
from scripts.recorder_soak_checks import DATABASE_LIMIT, WAL_LIMIT
from tests import test_runtime_steady as harness


def measured_report():
    return {
        "status": "passed", "stage": "complete", "seconds": 10, "elapsed": 10.1,
        "heartbeats": 2, "recoveries": 1, "heartbeat_max_gap": 5,
        "transport": "serial_loopback", "measurement_changes": 2,
        "recorder": {
            "entities": 44, "queue_peak": 20, "queue_after_drain": 0,
            "database_peak_bytes": 500000, "wal_peak_bytes": 100000,
            "purges": 1, "vacuums": 1, "deleted_rows": 10,
            "maintenance_max_seconds": 0.1, "integrity": "ok",
        },
        "samples": [dict.fromkeys((*LIMITS, "retained_clients"), 0) | {
            "phase": "steady", "serial_recoveries": 1, "recorder_inserted": 20,
            "recorder_states": 50, "recorder_attribute_growth": 0,
            "recorded_energy_rows": 3, "recorded_power_rows": 3,
            "database_bytes": 500000, "wal_bytes": 100000,
        }
                    for _ in range(3)],
    }


def runner(monkeypatch, tmp_path, data, *, returncode=0, exception=None):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    gate = importlib.import_module("check_runtime_steady")
    output = tmp_path / "output.json"
    output.write_text(json.dumps(measured_report()))
    monkeypatch.setattr("sys.argv", ["check_runtime_steady.py", "--seconds", "10",
                                    "--json-output", str(output)])

    def run(command, *, env, **_kwargs):
        assert Path(env["AMTRON_STEADY_REPORT"]) == output
        assert json.loads(output.read_text())["status"] == "running"
        if data is not None:
            Path(env["AMTRON_STEADY_REPORT"]).write_text(
                data if isinstance(data, str) else json.dumps(data)
            )
        if exception is not None:
            raise exception
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(gate.subprocess, "run", run)
    result = gate.main()
    return result, json.loads(output.read_text())


def test_success_requires_complete_valid_measurements(monkeypatch, tmp_path):
    result, report = runner(monkeypatch, tmp_path, measured_report())
    assert result == 0
    assert report["status"] == "passed"
    assert report["validation_errors"] == []


def test_pytest_failure_retains_samples_even_when_measurements_pass(monkeypatch, tmp_path):
    data = measured_report()
    result, report = runner(monkeypatch, tmp_path, data, returncode=1)
    assert result == 1
    assert report["status"] == "failed"
    assert report["samples"] == data["samples"]
    assert report["heartbeats"] == 2
    assert report["failure_type"] == "test_failure"


def test_timeout_retains_last_checkpoint(monkeypatch, tmp_path):
    data = measured_report() | {"status": "running", "stage": "running", "elapsed": 6}
    result, report = runner(monkeypatch, tmp_path, data,
                            exception=subprocess.TimeoutExpired("pytest", 130))
    assert result == 124
    assert report["status"] == "failed"
    assert report["failure_type"] == "timeout"
    assert report["samples"] == data["samples"]
    assert report["elapsed"] == 6


def test_interrupted_wrapper_leaves_checkpoint_at_artifact_path(monkeypatch, tmp_path):
    data = measured_report() | {"status": "running", "stage": "running", "elapsed": 6}
    with pytest.raises(KeyboardInterrupt):
        runner(monkeypatch, tmp_path, data, exception=KeyboardInterrupt())
    report = json.loads((tmp_path / "output.json").read_text())
    assert report == data


@pytest.mark.parametrize("data", [None, "{", "[]", '{"samples":null}'])
def test_missing_or_invalid_report_still_produces_failure_artifact(monkeypatch, tmp_path, data):
    result, report = runner(monkeypatch, tmp_path, data)
    assert result == 1
    assert report["status"] == "failed"
    assert report["validation_errors"]


def test_process_start_failure_produces_failure_artifact(monkeypatch, tmp_path):
    result, report = runner(monkeypatch, tmp_path, None, exception=OSError("cannot start"))
    assert result == 1
    assert report["failure_type"] == "process_start_failed"


@pytest.mark.parametrize(("key", "value"), [
    ("heartbeat_max_gap", 15), ("heartbeat_max_gap", None),
    ("heartbeat_max_gap", float("nan")), ("heartbeats", 0),
    ("recoveries", 0), ("measurement_changes", 0), ("elapsed", 9),
    ("transport", "fake"), ("samples", [None]),
])
def test_invalid_measurements_fail_and_remain_reviewable(monkeypatch, tmp_path, key, value):
    result, report = runner(monkeypatch, tmp_path, measured_report() | {key: value})
    assert result == 1
    assert report["status"] == "failed"
    assert report["validation_errors"]


@pytest.mark.parametrize(("key", "value"), [
    ("entities", 0), ("purges", 0), ("vacuums", 0), ("deleted_rows", 0),
    ("integrity", "corrupt"), ("queue_peak", 257), ("queue_after_drain", 1),
    ("database_peak_bytes", DATABASE_LIMIT + 1), ("wal_peak_bytes", WAL_LIMIT + 1),
    ("maintenance_max_seconds", float("inf")), ("maintenance_max_seconds", 0),
])
def test_recorder_maintenance_and_backlog_failures_reject_the_run(monkeypatch, tmp_path, key, value):
    data = measured_report()
    data["recorder"][key] = value
    result, report = runner(monkeypatch, tmp_path, data)
    assert result == 1
    assert report["status"] == "failed"
    assert report["validation_errors"]


@pytest.mark.parametrize(("key", "value"), [
    ("recorder_inserted", 10000), ("recorder_inserted", None),
    ("recorder_attribute_growth", 3), ("serial_recoveries", -1),
    ("recorder_states", 0), ("recorded_energy_rows", 0), ("recorded_power_rows", 0),
    ("database_bytes", DATABASE_LIMIT + 1), ("wal_bytes", WAL_LIMIT + 1),
])
def test_recorder_growth_and_lost_history_fail_even_after_a_later_good_sample(monkeypatch, tmp_path, key, value):
    data = measured_report()
    data["samples"][1][key] = value
    result, report = runner(monkeypatch, tmp_path, data)
    assert result == 1
    assert report["status"] == "failed"
    assert report["validation_errors"]


def test_missing_recorder_cannot_pass_a_healthy_serial_run(monkeypatch, tmp_path):
    data = measured_report()
    del data["recorder"]
    result, report = runner(monkeypatch, tmp_path, data)
    assert result == 1
    assert "Recorder measurements are missing" in report["validation_errors"]


def test_real_harness_checkpoints_and_reports_failure(monkeypatch, tmp_path, request):
    output = tmp_path / "checkpoint.json"
    monkeypatch.setenv("AMTRON_STEADY_SECONDS", "0.1")
    monkeypatch.setenv("AMTRON_STEADY_REPORT", str(output))
    original = harness._sample
    calls = 0
    checkpoint = None

    def fail_after_sample(*args):
        nonlocal calls, checkpoint
        calls += 1
        if calls == 2:
            checkpoint = json.loads(output.read_text())
            assert checkpoint["status"] == "running"
            assert len(checkpoint["samples"]) == 1
            raise RuntimeError("injected measurement failure")
        return original(*args)

    monkeypatch.setattr(harness, "_sample", fail_after_sample)
    with pytest.raises(RuntimeError, match="injected measurement failure"):
        harness.test_continuously_loaded_resources_stay_bounded(tmp_path, request)
    report = json.loads(output.read_text())
    assert report["status"] == "failed"
    assert report["failure_type"] == "RuntimeError"
    assert report["samples"] == checkpoint["samples"]
    assert report["stage"] == "running"
    assert not tracemalloc.is_tracing()
