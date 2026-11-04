"""The event history is bounded, passive, sanitized and separate per entry."""

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.data import WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.heartbeat import HeartbeatTask
from custom_components.mennekes_amtron_rtu.device.runtime_events import (
    MAX_EVENTS,
    EventTimeline,
    EventType,
    sanitize_events,
)
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.test_heartbeat import _client


def test_history_is_bounded_copied_and_entry_local():
    now = [0.0]
    history = EventTimeline(lambda: now[0])
    for index in range(100):
        now[0] = float(index)
        history.record(EventType.WRITE_RATE_LIMITED, register=R.CHARGING_CURRENT_EMS.address)
    snapshot = history.snapshot()
    assert len(snapshot) == MAX_EVENTS
    assert snapshot[0]["seconds_since_load"] == 60
    snapshot.clear()
    assert len(history.snapshot()) == MAX_EVENTS
    assert WriteDiagnostics().timeline.snapshot() == []


def test_remote_history_accepts_only_the_bounded_schema_and_copies_rows():
    history = EventTimeline(lambda: 3.0)
    for index, event in enumerate(EventType):
        history.record(event, register=None if index % 2 else 0x0D00)
    source = history.snapshot()
    result = sanitize_events(source)
    assert result == source
    assert result is not source
    result[0]["event"] = "changed"
    assert source[0]["event"] == "connection_lost"
    assert sanitize_events([]) == []
    row = source[0]
    assert len(sanitize_events([row] * MAX_EVENTS)) == MAX_EVENTS
    assert sanitize_events([row] * (MAX_EVENTS + 1)) is None
    assert sanitize_events([{**row, "seconds_since_load": 1}]) is not None
    assert sanitize_events([{**row, "register_address": 65535}]) is not None


@pytest.mark.parametrize("value", [None, {}, "private", [None], [[1]], [{"secret": "token"}]])
def test_remote_history_rejects_non_schema_containers(value):
    assert sanitize_events(value) is None


@pytest.mark.parametrize(("key", "value"), [
    ("seconds_since_load", -1), ("seconds_since_load", -0.5),
    ("seconds_since_load", float("nan")), ("seconds_since_load", float("inf")),
    ("seconds_since_load", True), ("seconds_since_load", "1"),
    ("event", "/dev/private"), ("event", None), ("event", {}),
    ("register_address", -1), ("register_address", 65536),
    ("register_address", True), ("register_address", "3328"),
    ("register_address", 1.0), ("secret", "token"),
])
def test_remote_history_rejects_unsafe_or_invalid_metadata(key, value):
    row = {"seconds_since_load": 1.5, "event": "heartbeat_failed", "register_address": 3328}
    row[key] = value
    assert sanitize_events([row]) is None


@pytest.mark.parametrize("register", [-1, 65536, True, "/dev/private"])
def test_history_never_accepts_identifying_text_or_invalid_addresses(register):
    with pytest.raises(ValueError, match="register address"):
        EventTimeline().record(EventType.WRITE_REJECTED, register=register)
    with pytest.raises(ValueError, match="predefined"):
        EventTimeline().record("raw exception with a device path")


def test_heartbeat_records_transitions_and_lateness_without_poll_noise():
    async def run():
        transport = FakeModbusClient(device_bank())
        diagnostics = WriteDiagnostics()
        client = _client(transport)
        await client.async_connect()
        heartbeat = HeartbeatTask(client, diagnostics)
        try:
            assert await heartbeat.async_beat_once()
            assert diagnostics.timeline.snapshot() == []
            transport.write_exceptions[R.HEARTBEAT.address] = raise_connection_error()
            for _ in range(3):
                assert not await heartbeat.async_beat_once()
            transport.write_exceptions.clear()
            assert await heartbeat.async_beat_once()
            heartbeat._note_delivery(0)
            heartbeat._note_delivery(11)
            assert [item["event"] for item in diagnostics.timeline.snapshot()] == [
                "heartbeat_failed", "heartbeat_recovered", "heartbeat_late",
            ]
        finally:
            await client.async_close()
    asyncio.run(run())
