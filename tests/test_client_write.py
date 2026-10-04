from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron._client_write import (
    WriteRateLimiter,
    validate_write,
    write_register,
)
from custom_components.mennekes_amtron.client_errors import (
    AmtronConnectionError,
    AmtronProtocolError,
    AmtronWriteRejectedError,
)
from tests.fakes import (
    FakeModbusClient,
    device_bank,
    raise_connection_error,
    raise_modbus_error,
)


def test_a_read_only_register_cannot_be_written() -> None:
    with pytest.raises(AmtronWriteRejectedError, match="not writable"):
        validate_write(R.EVSE_STATE, 1)


def test_an_enum_register_rejects_undocumented_values() -> None:
    validate_write(R.SOLAR_CHARGING_MODE, 2)
    with pytest.raises(AmtronWriteRejectedError, match="accepts only"):
        validate_write(R.SOLAR_CHARGING_MODE, 9)


def test_single_registers_use_function_six() -> None:
    transport = FakeModbusClient(device_bank())
    asyncio.run(
        write_register(transport, spec=R.CHARGING_RELEASE, value=1, device_id=50)
    )
    assert transport.writes == [(R.CHARGING_RELEASE.address, [1])]


def test_float_registers_use_function_sixteen() -> None:
    transport = FakeModbusClient(device_bank())
    asyncio.run(
        write_register(
            transport, spec=R.CHARGING_CURRENT_EMS, value=6.0, device_id=50
        )
    )
    address, words = transport.writes[0]
    assert address == R.CHARGING_CURRENT_EMS.address
    assert len(words) == 2


def test_a_dead_link_becomes_a_connection_error() -> None:
    transport = FakeModbusClient()
    transport.write_exceptions[R.CHARGING_RELEASE.address] = raise_connection_error()
    with pytest.raises(AmtronConnectionError):
        asyncio.run(
            write_register(transport, spec=R.CHARGING_RELEASE, value=1, device_id=50)
        )


def test_a_protocol_error_becomes_a_protocol_error() -> None:
    transport = FakeModbusClient()
    transport.write_exceptions[R.CHARGING_RELEASE.address] = raise_modbus_error()
    with pytest.raises(AmtronProtocolError):
        asyncio.run(
            write_register(transport, spec=R.CHARGING_RELEASE, value=1, device_id=50)
        )


def test_a_refusing_wallbox_is_reported_as_a_rejected_write() -> None:
    transport = FakeModbusClient()
    transport.write_exceptions[R.CHARGING_RELEASE.address] = "error"
    with pytest.raises(AmtronWriteRejectedError, match="0x0D05"):
        asyncio.run(
            write_register(transport, spec=R.CHARGING_RELEASE, value=1, device_id=50)
        )


def test_an_illegal_function_response_is_a_rejected_write() -> None:
    transport = FakeModbusClient()
    transport.write_exceptions[R.CHARGING_RELEASE.address] = 0x01
    with pytest.raises(AmtronWriteRejectedError):
        asyncio.run(
            write_register(transport, spec=R.CHARGING_RELEASE, value=1, device_id=50)
        )


def test_the_rate_limiter_counts_from_the_last_write() -> None:
    limiter = WriteRateLimiter()
    assert limiter.remaining("a", now=10.0, min_interval=5.0) == 0.0
    limiter.record("a", now=10.0)
    assert limiter.remaining("a", now=12.0, min_interval=5.0) == pytest.approx(3.0)
    assert limiter.remaining("a", now=16.0, min_interval=5.0) == 0.0
    assert limiter.remaining("a", now=12.0, min_interval=0.0) == 0.0
    assert limiter.remaining("b", now=12.0, min_interval=5.0) == 0.0


def test_forgetting_a_register_clears_its_interval() -> None:
    limiter = WriteRateLimiter()
    limiter.record("a", now=10.0)
    limiter.forget("a")
    limiter.forget("missing")
    assert limiter.remaining("a", now=10.5, min_interval=5.0) == 0.0
