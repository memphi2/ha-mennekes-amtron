"""Watch the meter and the plug cycles, to measure what a charge delivered.

One watch per wallbox survives a controlled entry reload. It keeps the meter
readings, the last complete plug cycle Home Assistant saw from plug-in to
unplug, and the vehicle profile recognized for that whole cycle. This is
evidence for an estimate, never a charging permission.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .device.const import DOMAIN
from .device.enums import EvseState
from .device.meter_log import MeterLog
from .device.presence_gap import PresenceGap
from .device.registers import ENERGY_TOTAL, EVSE_STATE
from .device.vehicle import snapshot_vehicle_connected
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
    """Meter readings, the running plug cycle and the last complete one."""

    def __init__(self) -> None:
        self._identity: str | None = None
        self._bound = False
        self._forget()

    def _forget(self) -> None:
        self.meter = MeterLog()
        self.last: PlugCycle | None = None
        self._gap = PresenceGap()
        self._lose_cycle()

    def _lose_cycle(self) -> None:
        """Nobody saw the whole cycle any more: it cannot be measured or owned."""
        self._present: bool | None = None
        self._plugged_at: float | None = None
        self._charging_at: float | None = None
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
        coordinator = runtime.coordinator
        data = coordinator.current_data()
        if not data.values and coordinator.last_update_success:
            # A replacement coordinator has not polled yet. Its empty initial
            # value is not evidence that the retained physical link was lost.
            return
        connected = coordinator.last_update_success and (
            (client := getattr(runtime, "client", None)) is None or client.connected
        )
        energy = data.get(ENERGY_TOTAL.key)
        if (
            connected and data.energy_read and data.energy_observed_at is not None
            and isinstance(energy, (int, float)) and not isinstance(energy, bool)
        ):
            self.meter.observe(data.energy_observed_at, float(energy))
        present = (
            snapshot_vehicle_connected(data.get, runtime.identity.layout_version)
            if connected else None
        )
        now = time.time()
        if self._gap.observe(now, known=present is not None):
            self._lose_cycle()
        if present is None:
            return
        if not present:
            if self._present and self._plugged_at is not None:
                claim = None if self._conflicted else self._claim
                self.last = PlugCycle(
                    self._plugged_at, now, claim.profile if claim is not None else None,
                )
            self._lose_cycle()
            self._present = False
            return
        if self._present is False:
            self._plugged_at = now
        self._present = True
        if self._charging_at is None and data.get(EVSE_STATE.key) == EvseState.CHARGING:
            self._charging_at = now
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
        if self._present:
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
