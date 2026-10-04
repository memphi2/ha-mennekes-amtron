from __future__ import annotations

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.decode import (
    decode_block,
    decode_register,
    encode_register,
    enum_option,
)
from custom_components.mennekes_amtron.register_blocks import REGISTER_BLOCKS


def test_float32_is_decoded_big_endian() -> None:
    # 16.0 as IEEE-754 single precision is 0x41800000.
    assert decode_register(R.MAX_EVSE_CURRENT, [0x4180, 0x0000]) == 16.0


def test_float32_round_trips_through_the_encoder() -> None:
    words = encode_register(R.CHARGING_CURRENT_EMS, 6.78)
    assert len(words) == 2
    assert decode_register(R.CHARGING_CURRENT_EMS, words) == pytest.approx(6.78)


def test_uint32_is_decoded_big_endian() -> None:
    assert decode_register(R.SESSION_DURATION, [0x0001, 0x86A0]) == 100000


def test_ascii_strips_trailing_nulls() -> None:
    words = [0x3133, 0x3133, 0x3230, 0x3132, 0x3035, 0x0000, 0x0000, 0x0000]
    assert decode_register(R.ARTICLE_NUMBER, words) == "1313201205"


def test_ascii_of_only_padding_is_none() -> None:
    assert decode_register(R.SERIAL_NUMBER, [0] * 8) is None


def test_non_finite_float_is_rejected() -> None:
    assert decode_register(R.POWER_TOTAL, [0x7FC0, 0x0000]) is None
    assert decode_register(R.POWER_TOTAL, [0x7F80, 0x0000]) is None


def test_short_read_is_rejected() -> None:
    assert decode_register(R.POWER_TOTAL, [0x0001]) is None


def test_uint16_passes_through() -> None:
    assert decode_register(R.EVSE_STATE, [5]) == 5


def test_block_decoding_uses_register_offsets() -> None:
    block = next(block for block in REGISTER_BLOCKS if block.name == "current_limits")
    words = (
        encode_register(R.DOWNGRADE_CURRENT, 8.0)
        + encode_register(R.CHARGING_CURRENT_EMS, 6.0)
        + encode_register(R.MAX_CURRENT_HOUSE, 32.0)
        + encode_register(R.MAX_EVSE_CURRENT, 16.0)
    )
    decoded = decode_block(block, words)
    assert decoded == {
        "downgrade_current": 8.0,
        "charging_current_ems": 6.0,
        "max_current_house": 32.0,
        "max_evse_current": 16.0,
    }


def test_encoding_a_uint16_out_of_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="uint16"):
        encode_register(R.CHARGING_RELEASE, 70000)


def test_encoding_a_string_register_is_rejected() -> None:
    with pytest.raises(ValueError, match="not writable"):
        encode_register(R.SERIAL_NUMBER, 1)


def test_enum_option_maps_register_values() -> None:
    assert enum_option(R.EVSE_STATE, 5) == "charging"
    assert enum_option(R.EVSE_STATE, 99) is None
    assert enum_option(R.POWER_TOTAL, 1.0) is None
