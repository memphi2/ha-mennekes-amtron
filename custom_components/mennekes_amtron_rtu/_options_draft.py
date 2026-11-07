"""Private section edits, merged without replacing unrelated live options."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


class OptionsChangedError(ValueError):
    """The section changed elsewhere while its form was open."""


class OptionsDraft:
    """Own a bounded set of keys until the user explicitly saves the section."""

    def __init__(
        self, section: str, current: Mapping[str, Any], keys: set[str]
    ) -> None:
        self.section = section
        self.keys = keys
        self.original = deepcopy(dict(current))
        self.values = deepcopy(dict(current))

    def clear(self, keys: set[str]) -> None:
        for key in keys:
            self.values.pop(key, None)

    def merge(self, current: Mapping[str, Any]) -> dict[str, Any]:
        """Preserve other sections and refuse to overwrite concurrent edits."""
        for key in self.keys:
            if (key in current) != (key in self.original) or current.get(
                key
            ) != self.original.get(key):
                raise OptionsChangedError
        merged = deepcopy(dict(current))
        for key in self.keys:
            if key in self.values:
                merged[key] = deepcopy(self.values[key])
            else:
                merged.pop(key, None)
        return merged
