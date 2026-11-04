"""Stable, transport independent command failures."""

from __future__ import annotations


class DeviceError(Exception):
    """A command could not be completed; the outcome may be uncertain."""

    def __init__(self, key: str, placeholders: dict[str, str] | None = None) -> None:
        super().__init__(key)
        self.translation_key = key
        self.translation_placeholders = placeholders


class DeviceValidationError(DeviceError):
    """A command was refused before it could be performed."""


def device_error(key: str, placeholders: dict[str, str] | None = None) -> DeviceError:
    return DeviceError(key, placeholders)


def validation_error(
    key: str, placeholders: dict[str, str] | None = None,
) -> DeviceValidationError:
    return DeviceValidationError(key, placeholders)
