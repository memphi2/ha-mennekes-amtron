"""Passive live debugging stays bounded, temporary, redacted and bus-neutral."""

from __future__ import annotations

import asyncio
import json

import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException

from custom_components.mennekes_amtron_rtu.device import modbus_trace
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device._client_read import read_words
from custom_components.mennekes_amtron_rtu.device._client_write import write_register
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronProtocolError,
    AmtronWriteRejectedError,
)
from custom_components.mennekes_amtron_rtu.device.modbus_trace import ModbusTrace
from tests.fakes import FakeModbusClient, device_bank, put


def test_disabled_capture_does_not_retain_any_observations():
    async def check():
        trace = ModbusTrace()
        transport = FakeModbusClient(device_bank())
        await read_words(transport, address=R.EVSE_STATE.address, count=1, device_id=50, trace=trace)
        await write_register(transport, spec=R.HEARTBEAT, value=0x55AA, device_id=50, trace=trace)
        assert trace.snapshot() == {"available": True, "enabled": False, "expires_in": 0, "latest_sequence": 0, "generation": 0, "records": []}
        assert len(transport.reads) == len(transport.writes) == 1
    asyncio.run(check())


def test_ring_bound_and_decoded_values_without_extra_bus_requests():
    async def check():
        now = [10.0]
        trace = ModbusTrace(clock=lambda: now[0])
        trace.set_enabled(True)
        transport = FakeModbusClient(device_bank())
        for _ in range(250):
            await read_words(transport, address=R.CURRENT_L1.address, count=R.CURRENT_L1.count, device_id=50, trace=trace)
        await write_register(transport, spec=R.CHARGING_CURRENT_EMS, value=12.5, device_id=50, trace=trace)
        await write_register(transport, spec=R.HEARTBEAT, value=0x55AA, device_id=50, trace=trace)
        snapshot = trace.snapshot()
        assert len(snapshot["records"]) == 200
        assert snapshot["records"][0]["sequence"] == 53
        assert len(transport.reads) == 250
        assert len(transport.writes) == 2
        assert snapshot["records"][-2]["values"][0]["value"] == 12.5
        assert snapshot["records"][-2]["function"] == "0x10"
        heartbeat = snapshot["records"][-1]
        assert heartbeat["operation"] == "heartbeat"
        assert heartbeat["address"] == "0x0D00"
        assert heartbeat["function"] == "0x06"
        assert heartbeat["values"][0]["register"] == "heartbeat"
        assert trace.snapshot(after=251)["records"] == [heartbeat]
        now[0] += 25
        assert trace.snapshot()["expires_in"] == 575
        # The open log page asks again: the capture goes on and keeps its rows.
        trace.set_enabled(True)
        assert trace.snapshot()["expires_in"] == 600
        assert len(trace.snapshot()["records"]) == 200
        now[0] += 25
        trace.clear()
        assert trace.snapshot()["records"] == []
        assert trace.snapshot()["expires_in"] == 575
        trace.close()
    asyncio.run(check())


def test_deadline_and_capture_generation_discard_old_transactions():
    async def check():
        now = [10.0]
        trace = ModbusTrace(clock=lambda: now[0])
        trace.set_enabled(True)
        first = trace.start()
        trace.clear()
        trace.finish(first, operation="read", address=R.EVSE_STATE.address, count=1, device_id=50, words=[1], error=None)
        assert trace.snapshot()["records"] == []
        second = trace.start()
        now[0] += 600
        trace.finish(second, operation="read", address=R.EVSE_STATE.address, count=1, device_id=50, words=[1], error=None)
        assert trace.snapshot()["enabled"] is False
        assert trace.snapshot()["records"] == []
        trace.set_enabled(True)
        trace.finish(second, operation="read", address=R.EVSE_STATE.address, count=1, device_id=50, words=[1], error=None)
        assert trace.snapshot()["records"] == []
        trace.close()
    asyncio.run(check())


def test_timer_erases_capture_even_without_requests_or_bus_activity(monkeypatch):
    async def check():
        trace = ModbusTrace()
        monkeypatch.setattr(modbus_trace, "CAPTURE_SECONDS", 0.01)
        trace.set_enabled(True)
        trace.finish(trace.start(), operation="read", address=R.EVSE_STATE.address, count=1, device_id=50, words=[1], error=None)
        assert trace._records
        await asyncio.sleep(0.03)
        # Inspect storage without snapshot/start, so lazy expiry cannot mask a
        # missing timer while the browser is closed and the bus is unavailable.
        assert trace._deadline == 0
        assert not trace._records
        assert trace._timer is None
    asyncio.run(check())


@pytest.mark.parametrize(("offset", "count"), [(0, 8), (-1, 10), (1, 2)])
def test_serial_text_never_reaches_capture_even_inside_or_partial_blocks(offset, count):
    async def check():
        trace = ModbusTrace()
        trace.set_enabled(True)
        bank = device_bank()
        put(bank, R.SERIAL_NUMBER, "PRIVATE123456789")
        await read_words(FakeModbusClient(bank), address=R.SERIAL_NUMBER.address + offset,
                         count=count, device_id=50, trace=trace)
        snapshot = trace.snapshot()
        encoded = json.dumps(snapshot)
        assert "PRIVATE" not in encoded
        assert "123456789" not in encoded
        assert snapshot["records"][0]["values"][0] == {
            "register": "serial_number", "address": "0x0013", "redacted": True,
        }
        assert "words" not in encoded
        trace.close()
    asyncio.run(check())


def test_failed_read_attempt_is_retained_before_successful_retry():
    class RetryClient(FakeModbusClient):
        async def read_holding_registers(self, address, *, count=1, device_id=1):
            if not self.reads:
                self.reads.append((address, count))
                raise ModbusIOException("SECRET frame and token")
            return await super().read_holding_registers(address, count=count, device_id=device_id)

    async def check():
        trace = ModbusTrace()
        trace.set_enabled(True)
        transport = RetryClient(device_bank())
        await read_words(transport, address=R.EVSE_STATE.address, count=1, device_id=50, retries=1, trace=trace)
        records = trace.snapshot()["records"]
        assert len(transport.reads) == len(records) == 2
        assert records[0]["error"] == "timeout"
        assert records[0]["values"] == []
        assert records[1]["outcome"] == "ok"
        assert "SECRET" not in json.dumps(records)
        trace.close()
    asyncio.run(check())


@pytest.mark.parametrize(("failure", "error", "category", "code"), [
    (ConnectionException("SECRET endpoint"), AmtronConnectionError, "connection", None),
    (ModbusIOException("SECRET frame"), AmtronProtocolError, "timeout", None),
    (3, AmtronWriteRejectedError, "device_exception", 3),
])
def test_failed_writes_keep_requested_value_but_only_classified_errors(failure, error, category, code):
    async def check():
        trace = ModbusTrace()
        trace.set_enabled(True)
        transport = FakeModbusClient()
        transport.write_exceptions[R.CHARGING_RELEASE.address] = failure
        with pytest.raises(error):
            await write_register(transport, spec=R.CHARGING_RELEASE, value=1, device_id=50, trace=trace)
        record = trace.snapshot()["records"][0]
        assert record["outcome"] == "error"
        assert record["error"] == category
        assert record["exception_code"] == code
        assert record["values"][0]["value"] == 1
        assert "SECRET" not in json.dumps(record)
        trace.close()
    asyncio.run(check())


def test_cancelled_transaction_is_recorded_and_cancellation_preserved():
    async def check():
        trace = ModbusTrace()
        trace.set_enabled(True)
        transport = FakeModbusClient()
        transport.read_exceptions[R.EVSE_STATE.address] = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await read_words(transport, address=R.EVSE_STATE.address, count=1, device_id=50, trace=trace)
        assert trace.snapshot()["records"][0]["error"] == "cancelled"
        trace.close()
    asyncio.run(check())


def test_malformed_words_do_not_let_optional_observer_break_the_bus_operation():
    async def check():
        trace = ModbusTrace()
        trace.set_enabled(True)
        trace.finish(trace.start(), operation="read", address=R.CURRENT_L1.address,
                     count=2, device_id=50, words=[-1, 999999], error=None)
        assert trace.snapshot()["records"][0]["values"][0]["value"] is None
        trace.close()
    asyncio.run(check())
