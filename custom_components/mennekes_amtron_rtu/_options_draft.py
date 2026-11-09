"""Private section edits, merged without replacing unrelated live options."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    CONF_VEHICLE_PROFILES,
    RESET_PENDING,
    VehicleProfile,
    profile_options,
    profiles,
)


class OptionsChangedError(ValueError):
    """The section changed elsewhere while its form was open."""


class OptionsDraft:
    """Own a bounded set of keys until the user explicitly saves the section."""

    def __init__(
        self, section: str, current: Mapping[str, Any], keys: set[str],
        profile_id: str | None = None,
    ) -> None:
        self.section = section
        self.keys = keys
        self.original = deepcopy(dict(current))
        self._profiled = section in {"vehicle", "departure", "losses"} and CONF_VEHICLE_PROFILES in current
        self._profile_id = profile_id
        self._profile: VehicleProfile | None = None
        self._selected = False
        if self._profiled and profile_id is not None:
            self._profile = profiles(current).get(profile_id)
            self._selected = current.get(CONF_ACTIVE_VEHICLE_PROFILE) == profile_id
        self.values = (
            profile_options(current, profile_id) if self._profiled else deepcopy(dict(current))
        )

    def clear(self, keys: set[str]) -> None:
        for key in keys:
            self.values.pop(key, None)

    def merge(self, current: Mapping[str, Any]) -> dict[str, Any]:
        """Preserve other sections and refuse to overwrite concurrent edits."""
        profile_id = None
        keys = self.keys
        live, original = current, self.original
        if self._profiled:
            profile_id = self._checked_profile_id(current)
            live = profile_options(current, profile_id)
            original = profile_options(self.original, profile_id)
            if not self._selected:
                # Only the selected car has a trip to invalidate.
                keys = keys - {RESET_PENDING}
        for key in keys:
            if (key in live) != (key in original) or live.get(
                key
            ) != original.get(key):
                raise OptionsChangedError
        merged = deepcopy(dict(current))
        options = merged if profile_id is None else merged[CONF_VEHICLE_PROFILES][profile_id]["options"]
        for key in keys:
            # A profile owns feature settings; invalidating the trip is global.
            target = merged if key == RESET_PENDING else options
            if key in self.values:
                target[key] = deepcopy(self.values[key])
            else:
                target.pop(key, None)
        return merged

    def _checked_profile_id(self, current: Mapping[str, Any]) -> str:
        """Reject a changed car, and for the selected car even a switch away and back."""
        previous = self._profile
        profile = profiles(current).get(self._profile_id) if self._profile_id else None
        if profile is None or previous is None or (
            profile.id, profile.provider_id, profile.device_id, profile.identity
        ) != (previous.id, previous.provider_id, previous.device_id, previous.identity) or (
            current.get(CONF_ACTIVE_VEHICLE_PROFILE) == profile.id
        ) != self._selected or (self._selected and current.get(
            CONF_VEHICLE_PROFILE_SELECTED_AT
        ) != self.original.get(CONF_VEHICLE_PROFILE_SELECTED_AT)):
            raise OptionsChangedError
        return profile.id
