"""Preview and validate configuration without publishing credentials or writes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.mennekes_amtron_rtu.device.connection import SerialConfig
from custom_components.mennekes_amtron_rtu.device.runtime_settings import (
    HOT_FIELDS,
    RUNTIME_FIELDS,
    runtime_candidate,
    runtime_preview,
    runtime_values,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig

TOKEN = "isolated-runtime-credential-1234567890"  # noqa: S105 - fixture
SAVED = {"serial_port": "/dev/fake", "token": "stored-private-credential-1234567890"}


def test_candidate_preserves_unrelated_options_and_never_projects_credentials():
    original = {**SAVED, "read_only": True, "baudrate": "57600"}
    updated, candidate = runtime_candidate(original, {"scan_interval": 7}, token=TOKEN)
    assert updated == {**original, "scan_interval": 7}
    assert "scan_interval" not in original
    assert candidate.token == TOKEN
    assert candidate.connection == SerialConfig("/dev/fake")
    result = runtime_values(candidate)
    assert set(result) == set(RUNTIME_FIELDS)
    assert all(type(value) is float for value in result.values())
    assert "credential" not in str(result)


@pytest.mark.parametrize("changes", [None, [], {}, True, {"token": TOKEN}, {"read_only": False}, {"host": "other"}])
def test_scope_is_narrow_and_not_an_alternate_connection_editor(changes):
    with pytest.raises(ValueError, match="invalid runtime settings"):
        runtime_candidate(SAVED, changes, token=TOKEN)


@pytest.mark.parametrize("field", RUNTIME_FIELDS)
@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), "10"])
def test_invalid_values_fail_before_a_save(field, value):
    with pytest.raises(ValueError, match=field):
        runtime_candidate(SAVED, {field: value}, token=TOKEN)


@pytest.mark.parametrize(("field", "low", "high"), [
    ("scan_interval", 2, 300), ("measurement_interval", 2, 300),
    ("current_cap", 6, 32), ("state_change_interval_seconds", 0, 3600),
    ("phase_switch_grace_seconds", 0, 30),
])
def test_exact_boundaries_and_restart_labels(field, low, high):
    active = ServerConfig.from_dict({**SAVED, "token": TOKEN})
    for value in (low, high):
        _, candidate = runtime_candidate(SAVED, {field: value}, token=TOKEN)
        result = runtime_preview(active, active, candidate)
        assert result["proposed"][field] == value
        expected = "live" if field in HOT_FIELDS else "restart"
        assert all(change["effect"] == expected for change in result["changes"])
    with pytest.raises(ValueError, match=field):
        runtime_candidate(SAVED, {field: high + 1}, token=TOKEN)


def test_native_pending_changes_remain_visible_and_noop_is_empty():
    active = ServerConfig.from_dict({**SAVED, "token": TOKEN})
    assert runtime_preview(active, active, active)["changes"] == []
    saved = replace(active, connection=SerialConfig("/dev/other"), read_only=False, listen="127.0.0.2", port=8766, current_cap=16)
    result = runtime_preview(active, saved, saved)
    assert result["restart_required"]
    assert result["restart_fields"] == ["current_cap", "connection", "read_only", "listen", "port"]
    reverted = runtime_preview(active, saved, active)
    assert not reverted["restart_required"]
    assert reverted["changes"][0]["saved"] == 16
    assert reverted["changes"][0]["proposed"] == 6
