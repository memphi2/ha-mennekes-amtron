"""Write diagnostics retain bounded metadata, never payloads or normal traffic."""

import asyncio
import json

import pytest
from pymodbus.exceptions import ModbusIOException

from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
    AmtronWriteRejectedError,
)
from custom_components.mennekes_amtron_rtu.device.write_trace import (
    MAX_WRITE_EVENTS,
    WriteTrace,
    sanitize_snapshot,
)


def test_write_trace_separates_queue_transaction_and_packet_timing():
    now = [10.0]
    trace = WriteTrace(lambda: now[0], packet_trace=True)
    trace.begin(address=0x0D00, queued=11.0, started=11.25)
    now[0] = 11.3
    packet = b"private payload serial-number token"
    assert trace.packet(True, packet) is packet
    now[0] = 11.4
    trace.packet(True, packet)
    now[0] = 11.45
    trace.packet(False, packet[:3])
    now[0] = 11.5
    trace.packet(False, packet[3:])
    now[0] = 12.25
    error = AmtronProtocolError("private exception /dev/serial/by-id/secret")
    error.__cause__ = ModbusIOException("untrusted transport message")
    trace.finish(error=error, connected=True)
    assert trace.snapshot() == [{
        "seconds_since_start": 2.25,
        "register_address": 0x0D00,
        "outcome": "io_error",
        "connected": True,
        "queue_wait_ms": 250.0,
        "transaction_ms": 1000.0,
        "tx_packets": 2,
        "rx_callbacks": 2,
        "first_tx_ms_from_start": 50.0,
        "last_rx_ms_from_start": 250.0,
    }]
    serialized = json.dumps(trace.snapshot())
    for secret in ("private", "payload", "serial-number", "token", "/dev/", "untrusted"):
        assert secret not in serialized


def test_trace_ignores_normal_success_and_recovers_only_the_failed_register():
    trace = WriteTrace(lambda: 0.0, packet_trace=True)
    for _ in range(100):
        trace.begin(address=0x0D00, queued=0, started=0)
        trace.packet(True, b"heartbeat")
        trace.packet(False, b"reply")
        trace.finish(error=None, connected=True)
    assert trace.snapshot() == []
    trace.begin(address=0x0D00, queued=0, started=0)
    trace.finish(error=AmtronProtocolError("missing reply"), connected=True)
    trace.begin(address=0x0302, queued=0, started=0)
    trace.finish(error=AmtronProtocolError("missing reply"), connected=True)
    for address in (0x0D01, 0x0302, 0x0302, 0x0D00, 0x0D00):
        trace.begin(address=address, queued=0, started=0)
        trace.finish(error=None, connected=True)
    assert [(event["register_address"], event["outcome"]) for event in trace.snapshot()] == [
        (0x0D00, "protocol_error"), (0x0302, "protocol_error"),
        (0x0302, "recovered"), (0x0D00, "recovered"),
    ]


@pytest.mark.parametrize(("error", "outcome"), [
    (AmtronConnectionError("secret"), "connection_error"),
    (AmtronProtocolError("secret"), "protocol_error"),
    (AmtronWriteRejectedError("secret"), "rejected"),
    (AmtronIllegalAddressError("secret"), "rejected"),
    (asyncio.CancelledError("secret"), "cancelled"),
    (RuntimeError("secret"), "unexpected_error"),
])
def test_failure_categories_never_use_exception_text(error, outcome):
    trace = WriteTrace(lambda: 0.0, packet_trace=False)
    trace.begin(address=0x0D00, queued=0, started=0)
    trace.finish(error=error, connected=False)
    assert trace.snapshot()[0]["outcome"] == outcome
    assert trace.snapshot()[0]["connected"] is False
    assert "secret" not in json.dumps(trace.snapshot())


def test_disabled_packet_tracing_is_explicitly_unknown():
    trace = WriteTrace(lambda: 0.0, packet_trace=False)
    trace.begin(address=0x0D00, queued=0, started=0)
    packet = b"unchanged"
    assert trace.packet(True, packet) is packet
    assert trace.packet(False, packet) is packet
    trace.finish(error=AmtronProtocolError("failure"), connected=True)
    assert {key: trace.snapshot()[0][key] for key in (
        "tx_packets", "rx_callbacks", "first_tx_ms_from_start", "last_rx_ms_from_start",
    )} == {
        "tx_packets": None, "rx_callbacks": None,
        "first_tx_ms_from_start": None, "last_rx_ms_from_start": None,
    }


def test_packet_counters_are_scoped_to_each_active_write():
    trace = WriteTrace(lambda: 0.0, packet_trace=True)
    packet = b"read-traffic"
    assert trace.packet(False, packet) is packet
    trace.finish(error=None, connected=True)
    trace.begin(address=0x0D00, queued=0, started=0)
    trace.packet(True, packet)
    trace.finish(error=AmtronProtocolError("failure"), connected=True)
    trace.packet(False, packet)
    trace.begin(address=0x0D00, queued=0, started=0)
    trace.finish(error=None, connected=True)
    first, second = trace.snapshot()
    assert (first["tx_packets"], first["rx_callbacks"]) == (1, 0)
    assert (second["tx_packets"], second["rx_callbacks"]) == (0, 0)
    assert first["last_rx_ms_from_start"] is None
    assert second["first_tx_ms_from_start"] is None


def test_history_is_bounded_copied_and_separate_per_client():
    now = [0.0]
    trace = WriteTrace(lambda: now[0], packet_trace=False)
    for number in range(100):
        now[0] = float(number)
        trace.begin(address=0x0D00, queued=now[0], started=now[0])
        trace.finish(error=AmtronProtocolError("failure"), connected=True)
    snapshot = trace.snapshot()
    assert len(snapshot) == MAX_WRITE_EVENTS
    assert snapshot[0]["seconds_since_start"] == 80.0
    snapshot[0]["outcome"] = "modified"
    snapshot.clear()
    assert trace.snapshot()[0]["outcome"] == "protocol_error"
    assert len(trace.snapshot()) == MAX_WRITE_EVENTS
    assert WriteTrace(lambda: 0.0, packet_trace=False).snapshot() == []


@pytest.mark.parametrize("address", [-1, 65536, True, "/dev/private"])
def test_only_register_addresses_can_enter_the_failed_register_set(address):
    trace = WriteTrace(lambda: 0.0, packet_trace=False)
    with pytest.raises(ValueError, match="register address"):
        trace.begin(address=address, queued=0, started=0)
    assert trace.snapshot() == []


def _row():
    trace = WriteTrace(lambda: 0.0, packet_trace=False)
    trace.begin(address=0x0D00, queued=0, started=0)
    trace.finish(error=AmtronProtocolError("private text"), connected=True)
    return trace.snapshot()[0]


def test_remote_snapshot_sanitizer_copies_exact_metadata():
    row = _row()
    row.update(tx_packets=1, rx_callbacks=2,
               first_tx_ms_from_start=0, last_rx_ms_from_start=3.25)
    source = [row]
    result = sanitize_snapshot(source)
    assert result == source
    assert result is not source
    assert result[0] is not row
    assert sanitize_snapshot([]) == []
    assert sanitize_snapshot([_row()]) == [_row()]


@pytest.mark.parametrize(("field", "value"), [
    ("register_address", True), ("register_address", -1),
    ("register_address", 65536), ("register_address", "0x0d00"),
    ("connected", 1), ("connected", "yes"),
    ("outcome", "untrusted /dev/private"), ("outcome", []),
    ("seconds_since_start", -0.1), ("seconds_since_start", float("nan")),
    ("queue_wait_ms", float("inf")), ("transaction_ms", True),
    ("transaction_ms", "private exception"), ("transaction_ms", None),
    ("first_tx_ms_from_start", False), ("last_rx_ms_from_start", -1),
    ("last_rx_ms_from_start", float("nan")),
    ("tx_packets", -1), ("tx_packets", 1.5), ("rx_callbacks", True),
])
def test_remote_snapshot_rejects_unknown_text_invalid_types_and_nonfinite_timings(field, value):
    row = _row()
    row[field] = value
    assert sanitize_snapshot([row]) is None


def test_remote_snapshot_rejects_extra_or_missing_fields_and_unbounded_rows():
    row = _row()
    assert sanitize_snapshot([row] * MAX_WRITE_EVENTS) is not None
    assert sanitize_snapshot([row] * (MAX_WRITE_EVENTS + 1)) is None
    assert sanitize_snapshot({}) is None
    assert sanitize_snapshot([None]) is None
    assert sanitize_snapshot([{**row, "secret": "token"}]) is None
    del row["outcome"]
    assert sanitize_snapshot([row]) is None
