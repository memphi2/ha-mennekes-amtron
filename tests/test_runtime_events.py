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
