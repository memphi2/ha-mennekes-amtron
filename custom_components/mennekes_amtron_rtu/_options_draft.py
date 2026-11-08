"""Private section edits, merged without replacing unrelated live options."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .vehicle_profiles import (
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    CONF_VEHICLE_PROFILES,
    RESET_PENDING,
    active_profile,
    vehicle_options,
)


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
        self._profiled = section in {"vehicle", "departure", "losses"} and CONF_VEHICLE_PROFILES in current
        self._profile = active_profile(current) if self._profiled else None
        self.values = vehicle_options(current) if self._profiled else deepcopy(dict(current))

    def clear(self, keys: set[str]) -> None:
        for key in keys:
            self.values.pop(key, None)

    def merge(self, current: Mapping[str, Any]) -> dict[str, Any]:
        """Preserve other sections and refuse to overwrite concurrent edits."""
        profile_id = None
        live, original = current, self.original
        if self._profiled:
            profile_id = self._checked_profile_id(current)
            live, original = vehicle_options(current), vehicle_options(self.original)
        for key in self.keys:
            if (key in live) != (key in original) or live.get(
                key
            ) != original.get(key):
                raise OptionsChangedError
        merged = deepcopy(dict(current))
        options = merged if profile_id is None else merged[CONF_VEHICLE_PROFILES][profile_id]["options"]
        for key in self.keys:
            # A profile owns feature settings; invalidating the trip is global.
            target = merged if key == RESET_PENDING else options
            if key in self.values:
                target[key] = deepcopy(self.values[key])
            else:
                target.pop(key, None)
        return merged

    def _checked_profile_id(self, current: Mapping[str, Any]) -> str:
        """Reject even a switch away and back before choosing the save target."""
        profile = active_profile(current)
        previous = self._profile
        if profile is None or previous is None or (
            profile.id, profile.provider_id, profile.device_id, profile.identity
        ) != (previous.id, previous.provider_id, previous.device_id, previous.identity) or current.get(
            CONF_VEHICLE_PROFILE_SELECTED_AT
        ) != self.original.get(CONF_VEHICLE_PROFILE_SELECTED_AT):
            raise OptionsChangedError
        return profile.id
