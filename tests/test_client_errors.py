from __future__ import annotations

from custom_components.mennekes_amtron.client_errors import (
    AmtronIllegalAddressError,
    AmtronProtocolError,
    AmtronRateLimitedError,
    AmtronWriteRejectedError,
    protocol_error_for,
)


def test_illegal_data_address_maps_to_its_own_error() -> None:
    error = protocol_error_for(0x02, 0x0114)
    assert isinstance(error, AmtronIllegalAddressError)
    assert "0x0114" in str(error)


def test_illegal_function_and_value_map_to_a_rejected_write() -> None:
    for code in (0x01, 0x03):
        error = protocol_error_for(code, 0x0D05)
        assert isinstance(error, AmtronWriteRejectedError)


def test_other_exception_codes_stay_generic() -> None:
    error = protocol_error_for(0x04, 0x0000)
    assert isinstance(error, AmtronProtocolError)
    assert not isinstance(error, AmtronIllegalAddressError)
    assert "4" in str(error)


def test_rate_limited_error_carries_the_wait_time() -> None:
    error = AmtronRateLimitedError("charging_current_ems", 2.5)
    assert error.key == "charging_current_ems"
    assert error.retry_after == 2.5
    assert "2.5" in str(error)
