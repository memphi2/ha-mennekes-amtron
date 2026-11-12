"""The register view consumes actual observations, never creates bus traffic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.decode import encode_register
from custom_components.mennekes_amtron_rtu.device.register_monitor import (
    RegisterMonitor,
)


@pytest.fixture
def monitor():
    clock = [100.0]
    start = datetime(2026, 11, 12, tzinfo=UTC)
    return RegisterMonitor(monotonic=lambda: clock[0], utcnow=lambda: start + timedelta(seconds=clock[0])), clock


def rows(monitor, **changes):
    options = {"layout": R.VALIDATED_LAYOUT, "available": True, "scan_interval": 5,
               "measurement_interval": 30, "idle": False, **changes}
    return {row["key"]: row for row in monitor.snapshot(**options)["registers"]}


def observe(monitor, spec, words=None, *, error=None, operation="read"):
    monitor.observe(operation=operation, address=spec.address, count=spec.count, words=words, error=error)


def test_inventory_has_all_specs_without_invented_measurements(monitor):
    view, _clock = monitor
    result = rows(view)
    assert set(result) == {spec.key for spec in R.REGISTERS}
    assert all(item["raw"] is None and item["updated_at"] is None for item in result.values())
    assert rows(view, layout=None)[R.EVSE_STATE.key]["reason"] == "layout_unknown"
    assert rows(view, layout=R.LAYOUT_V01_00)[R.CP_STATE.key]["status"] == "unsupported"
    assert result[R.HEARTBEAT.key]["status"] == "write_only"


def test_success_failure_staleness_recovery_and_clear(monitor):
    view, clock = monitor
    observe(view, R.EVSE_STATE, [5])
    before = rows(view)[R.EVSE_STATE.key]
    assert (before["status"], before["value"], before["enum"]) == ("ok", 5, "charging")
    assert before["raw"] == [5]
    clock[0] += 5
    observe(view, R.EVSE_STATE, error="timeout")
    failure = rows(view)[R.EVSE_STATE.key]
    assert failure["status"] == "degraded"
    assert failure["updated_at"] == before["updated_at"]
    assert failure["attempted_at"] != before["attempted_at"]
    assert failure["value"] == 5
    assert failure["consecutive_failures"] == 1
    clock[0] += 31
    assert rows(view)[R.EVSE_STATE.key]["status"] == "error"
    observe(view, R.EVSE_STATE, [1])
    assert rows(view)[R.EVSE_STATE.key]["consecutive_failures"] == 0
    assert rows(view, available=False)[R.EVSE_STATE.key]["reason"] == "wallbox_unavailable"
    clock[0] += 31
    assert rows(view)[R.EVSE_STATE.key]["reason"] == "overdue"
    view.clear()
    assert rows(view)[R.EVSE_STATE.key]["reason"] == "not_read"


@pytest.mark.parametrize("words", [None, [], [True], [-1], [65536], ["5"]])
def test_invalid_wire_values_remain_errors(words, monitor):
    view, _clock = monitor
    observe(view, R.EVSE_STATE, words)
    result = rows(view)[R.EVSE_STATE.key]
    assert result["status"] == "error"
    assert result["reason"] == "invalid_response"
    assert result["updated_at"] is None


def test_invalid_decoding_unknown_enums_and_wallbox_faults(monitor):
    view, _clock = monitor
    observe(view, R.POWER_TOTAL, [0x7FC0, 0])
    assert rows(view)[R.POWER_TOTAL.key]["reason"] == "invalid_value"
    observe(view, R.EVSE_STATE, [65535])
    assert rows(view)[R.EVSE_STATE.key]["reason"] == "unknown_enum"
    observe(view, R.ERROR_CODE, [200])
    assert rows(view)[R.ERROR_CODE.key]["status"] == "ok"
    observe(view, R.EVSE_STATE, error="connection")
    assert rows(view)[R.EVSE_STATE.key]["status"] == "degraded"


def test_identity_is_redacted_but_one_time_reads_do_not_become_stale(monitor):
    view, clock = monitor
    raw = b"PRIVATE-SERIAL".ljust(R.SERIAL_NUMBER.count * 2, b"\0")
    words = [int.from_bytes(raw[index:index + 2], "big") for index in range(0, len(raw), 2)]
    observe(view, R.SERIAL_NUMBER, words)
    clock[0] += 3600
    item = rows(view)[R.SERIAL_NUMBER.key]
    assert item["status"] == "ok"
    assert item["redacted"] and item["raw"] is None and item["value"] is None
    assert item["expected_interval_seconds"] is None
    observe(view, R.SERIAL_NUMBER, error="timeout")
    assert rows(view)[R.SERIAL_NUMBER.key]["status"] == "degraded"


def test_write_acknowledgement_does_not_overwrite_readback(monitor):
    view, clock = monitor
    words = encode_register(R.CHARGING_CURRENT_EMS, 8)
    observe(view, R.CHARGING_CURRENT_EMS, words)
    first = rows(view)[R.CHARGING_CURRENT_EMS.key]
    clock[0] += 5
    observe(view, R.CHARGING_CURRENT_EMS, encode_register(R.CHARGING_CURRENT_EMS, 16), operation="write")
    second = rows(view)[R.CHARGING_CURRENT_EMS.key]
    assert second["value"] == 8
    assert second["updated_at"] == first["updated_at"]
    assert second["write_attempted_at"] != first["attempted_at"]


def test_cadences_partial_ranges_and_unknown_operations(monitor):
    view, _clock = monitor
    view.observe(operation="login", address=0, count=10, words=[1] * 10, error=None)
    view.observe(operation="read", address=0, count=0, words=[], error=None)
    view.observe(operation="read", address=R.POWER_TOTAL.address + 1, count=1, words=[1], error=None)
    assert rows(view)[R.POWER_TOTAL.key]["reason"] == "not_read"
    assert rows(view, idle=True)[R.POWER_TOTAL.key]["expected_interval_seconds"] == 60
    assert rows(view, scan_interval=100)[R.EVSE_STATE.key]["expected_interval_seconds"] == 100
    assert rows(view, scan_interval=100)[R.POWER_TOTAL.key]["expected_interval_seconds"] == 100
    assert rows(view, scan_interval=100)[R.ENERGY_TOTAL.key]["expected_interval_seconds"] == 100
