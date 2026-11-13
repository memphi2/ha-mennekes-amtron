"""Register values folded into another entity's attributes.

A value that only explains or details an entity's state rides along as an
attribute instead of being an entity of its own. Each reader returns ``None``
when the register has no value, and ``None`` attributes are left out, so an
older layout simply shows fewer attributes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .data import WallboxData
from .device.decode import as_integer, as_number, enum_option
from .device.registers import REGISTERS_BY_KEY

type AttributeReader = Callable[[WallboxData], Any]
type AttributeReaders = Mapping[str, AttributeReader]


def number_attribute(key: str, precision: int) -> AttributeReader:
    """A measurement or setting, rounded like a sensor state would be."""
    def read(data: WallboxData) -> float | None:
        number = as_number(data.get(key))
        return None if number is None else round(number, precision)
    return read


def integer_attribute(key: str) -> AttributeReader:
    """A count or a code."""
    def read(data: WallboxData) -> int | None:
        return as_integer(data.get(key))
    return read


def minutes_attribute(key: str) -> AttributeReader:
    """A duration in seconds, shown in whole minutes: a ticking attribute would
    otherwise write a history row every poll."""
    def read(data: WallboxData) -> int | None:
        number = as_number(data.get(key))
        return None if number is None or number < 0 else int(number // 60)
    return read


def enum_attribute(key: str) -> AttributeReader:
    """The option name of an enumerated register, as its sensor would show it."""
    spec = REGISTERS_BY_KEY[key]
    return lambda data: enum_option(spec, data.get(key))


def flag_attribute(key: str) -> AttributeReader:
    """A 0/1 setting as a boolean."""
    def read(data: WallboxData) -> bool | None:
        value = as_integer(data.get(key))
        return None if value is None else value == 1
    return read


def under_load(reader: AttributeReader) -> AttributeReader:
    """A reading that matters only while power flows.

    Mains voltage wobbles by a volt all day; as an attribute of an idle sensor
    that wobble would write a history row every poll for nothing.
    """
    def read(data: WallboxData) -> Any:
        power = as_number(data.get("power_total"))
        return reader(data) if power is not None and power > 0 else None
    return read


def read_attributes(data: WallboxData, readers: AttributeReaders) -> dict[str, Any] | None:
    """Collect the attributes that have a value; ``None`` when none has."""
    attributes = {name: value for name, reader in readers.items() if (value := reader(data)) is not None}
    return attributes or None
