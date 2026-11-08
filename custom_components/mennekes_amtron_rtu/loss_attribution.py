"""Bound one optional vehicle attribution to an observed physical plug cycle.

This is transient evidence, not charging permission. It survives a controlled
entry reload only; completed session IDs prevent reusing it for another charge.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .device.charging_session import CompletedSession
from .device.const import DOMAIN
from .device.registers import CP_STATE, EVSE_STATE
from .device.vehicle import vehicle_connected
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

_DATA_KEY = f"{DOMAIN}_loss_attribution"


@dataclass(frozen=True, slots=True)
class _Claim:
    profile: VehicleProfile
    confirmed_at: float
    selected_at: float
    automatic: bool

    def fits(self, session: CompletedSession) -> bool:
        return (
            session.connected_at <= self.confirmed_at < session.disconnected_at
            and (self.automatic or session.started_after >= self.selected_at)
        )


class LossAttribution:
    """Keep one current claim and one completed result, never an event history."""

    def __init__(self) -> None:
        self._identity: str | None = None
        self._identity_bound = False
        self._present: bool | None = None
        self._claim: _Claim | None = None
        self._pending: _Claim | None = None
        self._conflicted = False
        self._anchored = False
        self._cycle_observed = False
        self._pending_witnessed = False
        self._seen_summary_id: str | None = None
        self._completed_id: str | None = None
        self._completed: _Claim | None = None

    def _invalidate(self) -> None:
        self._present = None
        self._claim = self._pending = None
        self._anchored = False
        self._conflicted |= self._cycle_observed or self._pending_witnessed

    def observe(self, entry: MennekesAmtronConfigEntry) -> None:
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            return
        identity = runtime.identity.serial_number
        if self._identity_bound and identity != self._identity:
            self._invalidate()
            self._completed = None
            self._completed_id = self._seen_summary_id = None
            self._cycle_observed = self._pending_witnessed = self._conflicted = False
        self._identity = identity
        self._identity_bound = True
        coordinator = runtime.coordinator
        data = coordinator.current_data()
        if not data.values and coordinator.last_update_success:
            # A replacement coordinator has not polled yet. Its empty initial
            # value is not evidence that the retained physical link was lost.
            return
        present = vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=runtime.identity.layout_version >= CP_STATE.min_layout,
        ) if coordinator.last_update_success and (
            (client := getattr(runtime, "client", None)) is None or client.connected
        ) else None
        if present is None:
            self._invalidate()
            return
        session = CompletedSession.parse(data.charging_session) if data.charging_session else None
        if session is not None and session.session_id != self._seen_summary_id:
            claim = self._claim or self._pending
            witnessed = self._cycle_observed or self._pending_witnessed
            self._completed = (
                claim if not self._conflicted and claim is not None and claim.fits(session)
                and profiles(entry.options).get(claim.profile.id) == claim.profile else None
            )
            self._seen_summary_id = session.session_id
            self._completed_id = session.session_id if witnessed else None
            # A new service summary can reveal a completed cycle even if HA
            # next observes another connected car after an entry reload.
            self._claim = self._pending = None
            self._conflicted = False
            self._cycle_observed = self._pending_witnessed = False
            self._anchored = witnessed or self._anchored
            self._present = False
        if present is False:
            if self._cycle_observed:
                self._pending = None if self._conflicted else self._claim
                self._pending_witnessed = True
                self._claim = None
                self._conflicted = False
                self._cycle_observed = False
            self._present = False
            self._anchored = True
            return
        if self._present is False:
            self._claim = self._pending = None
            self._pending_witnessed = False
            self._conflicted = False
        self._present = True
        self._cycle_observed = True
        claim = self._claim
        selected = active_profile(entry.options)
        if claim is not None and (
            profiles(entry.options).get(claim.profile.id) != claim.profile
            or (selected is not None and selected.id != claim.profile.id)
            or (selected is None and entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is not True)
        ):
            self._claim = None
            self._conflicted = True
        if entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is not True and selected is not None:
            self.confirm(entry, selected.id, automatic=False)

    def confirm(self, entry: MennekesAmtronConfigEntry, profile_id: str, *, automatic: bool) -> None:
        profile = profiles(entry.options).get(profile_id)
        if (
            self._present is not True or profile is None or self._conflicted
            or (automatic and not self._anchored)
        ):
            return
        if self._claim is not None:
            if self._claim.profile != profile:
                self._claim = None
                self._conflicted = True
            return
        selected_at = entry.options.get(CONF_VEHICLE_PROFILE_SELECTED_AT, float("inf"))
        self._claim = _Claim(
            profile, time.time(),
            float(selected_at) if type(selected_at) in (int, float) else float("inf"),
            automatic,
        )

    def owner(self, entry: MennekesAmtronConfigEntry) -> VehicleProfile | None:
        claim = (
            self._claim if self._present is True
            else self._pending or self._completed
        )
        if claim is None or self._conflicted or (
            self._present is True and not claim.automatic
        ):
            return None
        return claim.profile if profiles(entry.options).get(claim.profile.id) == claim.profile else None

    def owns(self, entry: MennekesAmtronConfigEntry, profile_id: str, session: CompletedSession) -> bool | None:
        if session.session_id != self._completed_id:
            return None
        return (
            self._completed is not None and self._completed.profile.id == profile_id
            and profiles(entry.options).get(profile_id) == self._completed.profile
        )


def _state(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> LossAttribution | None:
    states: dict[str, LossAttribution] = hass.data.get(_DATA_KEY, {})
    return states.get(getattr(entry, "entry_id", ""))


def track_loss_attribution(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> CALLBACK_TYPE:
    """Subscribe for any configured vehicle profiles; rebind after a reload."""
    states: dict[str, LossAttribution] = hass.data.setdefault(_DATA_KEY, {})
    attribution = states.setdefault(entry.entry_id, LossAttribution())

    @callback
    def updated() -> None:
        attribution.observe(entry)

    unsubscribe = entry.runtime_data.coordinator.async_add_listener(updated)
    updated()
    return unsubscribe


def forget_loss_attribution(hass: HomeAssistant, entry_id: str) -> None:
    """Discard incomplete provenance at a full unload, removal or failed setup."""
    hass.data.get(_DATA_KEY, {}).pop(entry_id, None)


def confirm_automatic_vehicle(hass: HomeAssistant, entry: MennekesAmtronConfigEntry, profile_id: str) -> None:
    if (attribution := _state(hass, entry)) is not None:
        attribution.observe(entry)
        attribution.confirm(entry, profile_id, automatic=True)


def loss_vehicle_options(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> dict[str, Any]:
    """Retain only loss settings for the recognized owner after auto deselection."""
    if (attribution := _state(hass, entry)) is not None:
        attribution.observe(entry)
        if (
            entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True
            and active_profile(entry.options) is None
            and (owner := attribution.owner(entry)) is not None
        ):
            return vehicle_options({**entry.options, CONF_ACTIVE_VEHICLE_PROFILE: owner.id})
    return vehicle_options(entry.options)


def attributed_session(hass: HomeAssistant, entry: MennekesAmtronConfigEntry, profile_id: str, session: CompletedSession) -> bool | None:
    """Return a witnessed ownership result, or None for legacy time-based rules."""
    if (attribution := _state(hass, entry)) is None:
        return None
    attribution.observe(entry)
    return attribution.owns(entry, profile_id, session)
