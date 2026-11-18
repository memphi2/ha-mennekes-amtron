"""Name the vehicle of a plug cycle the service measured.

The service keeps the meter log and sees each plug cycle from plug-in to
unplug. One watch per wallbox, surviving a controlled entry reload, adds what
only Home Assistant knows: the vehicle profile recognized for that whole
cycle. This is evidence for an estimate, never a charging permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .device.const import DOMAIN
from .entry_types import MennekesAmtronConfigEntry
from .vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    VehicleProfile,
    active_profile,
    profiles,
    vehicle_options,
)

_DATA_KEY = f"{DOMAIN}_charge_watch"


@dataclass(frozen=True, slots=True)
class PlugCycle:
    """A plug cycle seen from plug-in to unplug, and whose car it was."""

    plugged_at: float
    unplugged_at: float
    owner: VehicleProfile | None


@dataclass(frozen=True, slots=True)
class _Claim:
    profile: VehicleProfile
    automatic: bool


class ChargeWatch:
    """The claim on the running plug cycle and the last complete cycle."""

    def __init__(self) -> None:
        self._identity: str | None = None
        self._bound = False
        self._forget()

    def _forget(self) -> None:
        self.last: PlugCycle | None = None
        self._start_cycle(None, None)

    def _start_cycle(self, plugged_at: float | None, charging_at: float | None) -> None:
        self._plugged_at = plugged_at
        self._charging_at = charging_at
        self._claim: _Claim | None = None
        self._conflicted = False

    def observe(self, entry: MennekesAmtronConfigEntry) -> None:
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            return
        identity = runtime.identity.serial_number
        if self._bound and identity != self._identity:
            self._forget()
        self._identity, self._bound = identity, True
        meter = runtime.coordinator.current_data().charge_meter
        if meter is None:
            # Not polled yet, or a poll failed: the service judges the gap.
            return
        last = meter["last"]
        if last is not None and (
            self.last is None or (self.last.plugged_at, self.last.unplugged_at) != last
        ):
            claim = self._claim if self._plugged_at == last[0] and not self._conflicted else None
            self.last = PlugCycle(last[0], last[1], claim.profile if claim is not None else None)
        if meter["plugged_at"] != self._plugged_at:
            self._start_cycle(meter["plugged_at"], None)
        self._charging_at = meter["charging_at"]
        if self._plugged_at is None:
            return
        selected = active_profile(entry.options)
        automatic = entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True
        claim = self._claim
        if claim is not None and (
            profiles(entry.options).get(claim.profile.id) != claim.profile
            or (selected is not None and selected.id != claim.profile.id)
            or (selected is None and not automatic)
        ):
            self._claim = None
            self._conflicted = True
        if not automatic and selected is not None:
            self.confirm(entry, selected.id, automatic=False)

    def confirm(self, entry: MennekesAmtronConfigEntry, profile_id: str, *, automatic: bool) -> None:
        """Name the car of the running cycle; a second name makes it nobody's."""
        profile = profiles(entry.options).get(profile_id)
        if self._plugged_at is None or profile is None or self._conflicted:
            return
        if self._claim is not None:
            if self._claim.profile != profile:
                self._claim = None
                self._conflicted = True
            return
        charging_at = self._charging_at
        if not automatic and charging_at is not None:
            # A car selected by hand owns only a charge that began afterwards.
            selected_at = entry.options.get(CONF_VEHICLE_PROFILE_SELECTED_AT)
            if (
                not isinstance(selected_at, (int, float)) or isinstance(selected_at, bool)
                or selected_at > charging_at
            ):
                return
        self._claim = _Claim(profile, automatic)

    def owner(self, entry: MennekesAmtronConfigEntry) -> VehicleProfile | None:
        """The recognized car of the running cycle, else of the last one."""
        if self._plugged_at is not None:
            profile = (
                self._claim.profile
                if self._claim is not None and self._claim.automatic and not self._conflicted
                else None
            )
        else:
            profile = self.last.owner if self.last is not None else None
        if profile is None or profiles(entry.options).get(profile.id) != profile:
            return None
        return profile


def charge_watch(hass: HomeAssistant, entry_id: str) -> ChargeWatch:
    watches: dict[str, ChargeWatch] = hass.data.setdefault(_DATA_KEY, {})
    return watches.setdefault(entry_id, ChargeWatch())


def track_charge_watch(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> CALLBACK_TYPE:
    """Follow every poll of this entry; rebind after a reload."""
    watch = charge_watch(hass, entry.entry_id)

    @callback
    def updated() -> None:
        watch.observe(entry)

    unsubscribe = entry.runtime_data.coordinator.async_add_listener(updated)
    updated()
    return unsubscribe


def forget_charge_watch(hass: HomeAssistant, entry_id: str) -> None:
    """Discard the evidence at a full unload, removal or failed setup."""
    hass.data.get(_DATA_KEY, {}).pop(entry_id, None)


def confirm_automatic_vehicle(hass: HomeAssistant, entry: MennekesAmtronConfigEntry, profile_id: str) -> None:
    watch = charge_watch(hass, entry.entry_id)
    watch.observe(entry)
    watch.confirm(entry, profile_id, automatic=True)


def loss_vehicle_options(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> dict[str, Any]:
    """Retain only loss settings for the recognized owner after auto deselection."""
    watch = charge_watch(hass, getattr(entry, "entry_id", ""))
    watch.observe(entry)
    if (
        entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True
        and active_profile(entry.options) is None
        and (owner := watch.owner(entry)) is not None
    ):
        return vehicle_options({**entry.options, CONF_ACTIVE_VEHICLE_PROFILE: owner.id})
    return vehicle_options(entry.options)
