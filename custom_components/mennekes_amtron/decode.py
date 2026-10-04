"""Register word decoding and encoding.

Byte and word order are both big endian on this device, which is pymodbus'
default, so the conversion helpers are used without a word-order override.
Keeping every conversion here means the client never looks at raw words and a
decoding bug has exactly one place to live.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pymodbus.client.mixin import ModbusClientMixin

from .register_blocks import RegisterBlock
from .registers import RegisterDataType, RegisterSpec

type RegisterValue = int | float | str | None

_DATATYPES: Mapping[RegisterDataType, ModbusClientMixin.DATATYPE] = {
    RegisterDataType.UINT16: ModbusClientMixin.DATATYPE.UINT16,
    RegisterDataType.UINT32: ModbusClientMixin.DATATYPE.UINT32,
    RegisterDataType.FLOAT32: ModbusClientMixin.DATATYPE.FLOAT32,
    RegisterDataType.STRING: ModbusClientMixin.DATATYPE.STRING,
}


def decode_register(spec: RegisterSpec, words: Sequence[int]) -> RegisterValue:
    """Decode the words of one register range into a Python value.

    Returns ``None`` for a short read and for a float the device reports as
    NaN or infinity, so a broken frame never reaches an entity as a number.
    """

    if len(words) < spec.count:
        return None
    raw = list(words[: spec.count])
    value = ModbusClientMixin.convert_from_registers(raw, _DATATYPES[spec.datatype])
    if spec.datatype is RegisterDataType.STRING:
        return _clean_string(value)
    if spec.datatype is RegisterDataType.FLOAT32:
        return value if isinstance(value, float) and math.isfinite(value) else None
    return value if isinstance(value, int) else None


def decode_block(
    block: RegisterBlock, words: Sequence[int]
) -> dict[str, RegisterValue]:
    """Decode one block read into a register-key to value mapping."""

    decoded: dict[str, RegisterValue] = {}
    for spec in block.specs:
        offset = spec.address - block.address
        decoded[spec.key] = decode_register(spec, words[offset : offset + spec.count])
    return decoded


def encode_register(spec: RegisterSpec, value: float) -> list[int]:
    """Encode a value into the words of one register range."""

    if spec.datatype is RegisterDataType.FLOAT32:
        return [
            int(word)
            for word in ModbusClientMixin.convert_to_registers(
                float(value), _DATATYPES[spec.datatype]
            )
        ]
    if spec.datatype is RegisterDataType.UINT16:
        word = int(value)
        if not 0 <= word <= 0xFFFF:
            raise ValueError(f"{spec.key} value {value} does not fit a uint16 register")
        return [word]
    raise ValueError(f"{spec.key} is not writable as {spec.datatype}")


def enum_option(spec: RegisterSpec, value: RegisterValue) -> str | None:
    """Return the state option a register value maps to, if any."""

    if spec.enum_map is None or not isinstance(value, int):
        return None
    return spec.enum_map.get(value)


def _clean_string(value: object) -> str | None:
    text = str(value).split("\x00", 1)[0].strip()
    cleaned = "".join(char for char in text if char.isprintable())
    return cleaned or None
