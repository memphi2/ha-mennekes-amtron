"""Optional local vehicle association; it never sends a charging command."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .device.registers import CP_STATE, EVSE_STATE
from .device.vehicle import vehicle_connected
from .entry_types import MennekesAmtronConfigEntry
from .loss_attribution import confirm_automatic_vehicle
from .vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    VehicleProfile,
    activate_profile,
    active_profile,
    profiles,
    vehicle_options,
)
from .vehicle_sources import (
    DETECTION_MAX_AGE_SECONDS,
    VehicleDetection,
    VehicleDetectionSource,
    resolve_detection_source,
)

SETTLE_SECONDS = 3


def entity_signal(
    state: State | None, *, home: bool, connected_states: tuple[str, ...] = (),
    max_age: float = DETECTION_MAX_AGE_SECONDS,
) -> bool | None:
    """Only current, explicit HA presence/connection states can identify a car."""
    if state is None or state.attributes.get("restored") is True:
        return None
    age = (dt_util.utcnow() - state.last_reported).total_seconds()
    if not -5 <= age <= max_age:
        return None
    if state.domain == "binary_sensor":
        return {"on": True, "off": False}.get(state.state)
    if not home and state.domain == "sensor" and connected_states:
        if state.state in ("unknown", "unavailable", ""):
            return None
        return state.state in connected_states
    if home and state.domain in ("person", "device_tracker"):
        if state.state in ("unknown", "unavailable", ""):
            return None
        return state.state == "home"
    return None


class VehicleEvidence:
    """Shared local evidence without profile selection or charging side effects."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._profiles = profiles(entry.options)
        self._sources: dict[str, VehicleDetectionSource | None] = {}
        for key, profile in self._profiles.items():
            if profile.detection is not None and profile.detection.source == "profile":
                effective = vehicle_options({**entry.options, CONF_ACTIVE_VEHICLE_PROFILE: key})
                self._sources[key] = resolve_detection_source(hass, effective)

    def _snapshot(self, profile: VehicleProfile) -> VehicleDetection:
        detection = profile.detection
        if detection is None:
            return VehicleDetection()
        if detection.source == "profile":
            source = self._sources.get(profile.id)
            return source.snapshot if source is not None else VehicleDetection()
        connected = self._hass.states.get(detection.connected_entity)
        home = self._hass.states.get(detection.home_entity)
        return VehicleDetection(
            connected=entity_signal(
                connected, home=False, connected_states=detection.connected_states,
            ),
            home=entity_signal(home, home=True),
            connected_at=connected.last_reported if connected is not None else None,
            parked_home=entity_signal(home, home=True, max_age=math.inf),
        )

    def snapshot(self, profile_id: str) -> VehicleDetection:
        """Return evidence for an explicitly configured vehicle."""
        profile = self._profiles.get(profile_id)
        return self._snapshot(profile) if profile is not None else VehicleDetection()

    def current(self) -> bool:
        """An old observer cannot authorize a modified vehicle catalog."""
        return profiles(self._entry.options) == self._profiles

    def selected(self, plugged_at: datetime | None = None) -> str | None:
        """One positive car is insufficient if another configured car is unknown.

        ``plugged_at`` is when the wallbox saw the current car arrive; a car
        that reports itself plugged in then needs no fresh location.
        """
        candidates = []
        for key, profile in self._profiles.items():
            if profile.detection is None:
                continue
            match = self._snapshot(profile).at_home(plugged_at)
            if match is False:
                continue
            if match is None:
                return None
            candidates.append(key)
        return candidates[0] if len(candidates) == 1 else None

    def effective(
        self, active: str | None, *, wallbox_connected: bool | None = None, plugged_at: datetime | None = None,
    ) -> str | None:
        """Keep the active car until another one is identified or unknown.

        Unplugging or stale reports do not deselect it, so a briefly missing
        signal does not switch the car's goals off. Another car reporting
        itself plugged in, or a car at the wallbox while the active one reports itself elsewhere,
        ends the selection, so its goals never apply to another vehicle.
        """
        selected = self.selected(plugged_at)
        if selected is not None or active is None or (profile := self._profiles.get(active)) is None:
            return selected
        for key, other in self._profiles.items():
            if key != active and other.detection is not None and self._snapshot(other).at_home(plugged_at) is True:
                return None
        evidence = self._snapshot(profile)
        if wallbox_connected is True and (evidence.connected is False or evidence.home is False):
            return None
        return active

    def subscribe(self, on_change: Callable[..., None]) -> CALLBACK_TYPE:
        """Watch configured sources; callers decide what a match may do."""
        unsubs: list[CALLBACK_TYPE] = []
        entities = {
            entity
            for profile in self._profiles.values()
            if profile.detection is not None and profile.detection.source == "entities"
            for entity in (profile.detection.connected_entity, profile.detection.home_entity)
        }
        if entities:
            unsubs.extend((
                async_track_state_change_event(self._hass, entities, on_change),
                async_track_state_report_event(self._hass, entities, on_change),
            ))
        unsubs.extend(
            source.subscribe(on_change) for source in self._sources.values() if source is not None
        )
        unsubs.append(async_track_time_interval(self._hass, on_change, timedelta(minutes=1)))

        @callback
        def unsubscribe() -> None:
            for unsub in unsubs:
                unsub()
            unsubs.clear()

        return unsubscribe


class VehicleDetectionObserver(VehicleEvidence):
    """Optional automatic profile selection, without charging commands."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        super().__init__(hass, entry)
        self._unsubs: list[CALLBACK_TYPE] = []
        self._pending: CALLBACK_TYPE | None = None
        self._candidate: str | None = None
        self._generation = 0
        self._stopped = False

    def _current(self) -> bool:
        return (
            not self._stopped
            and self._entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True
            and self.current()
        )

    @callback
    def start(self) -> None:
        """Subscribe after setup; unchanged reports cause no writes."""
        self._unsubs.append(self.subscribe(self._changed))
        if (runtime := getattr(self._entry, "runtime_data", None)) is not None:
            self._unsubs.append(runtime.coordinator.async_add_listener(self._changed))
        self._changed()

    @callback
    def _changed(self, _event: object = None) -> None:
        if not self._current():
            self._cancel_pending()
            return
        active = self._entry.options.get(CONF_ACTIVE_VEHICLE_PROFILE)
        plugged_at = self._plugged_at()
        selected = self.effective(active, wallbox_connected=self._wallbox_connected(), plugged_at=plugged_at)
        if selected == active:
            if selected is not None and selected == self.selected(plugged_at):
                confirm_automatic_vehicle(self._hass, self._entry, selected)
            self._cancel_pending()
            return
        if selected is None:
            self._cancel_pending()
            self._apply(None)
        elif self._pending is None or selected != self._candidate:
            self._cancel_pending()
            # Do not keep applying the former car's limits while the new
            # candidate is settling. The reload re-arms the bounded timer.
            self._apply(None)
            self._candidate = selected
            generation = self._generation

            @callback
            def settled(now: object) -> None:
                if generation == self._generation:
                    self._settled(now)

            self._pending = async_call_later(self._hass, SETTLE_SECONDS, settled)

    @callback
    def _settled(self, _now: object) -> None:
        candidate = self._candidate
        self._pending = None
        self._candidate = None
        if self._current() and candidate == self.selected(self._plugged_at()):
            self._apply(candidate)

    def _plugged_at(self) -> datetime | None:
        runtime = getattr(self._entry, "runtime_data", None)
        binding = getattr(runtime, "vehicle_binding", None)
        return binding.plugged_at if binding is not None else None

    def _wallbox_connected(self) -> bool | None:
        runtime = getattr(self._entry, "runtime_data", None)
        if runtime is None or not runtime.coordinator.last_update_success:
            return None
        data = runtime.coordinator.current_data()
        return vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=runtime.identity.layout_version >= CP_STATE.min_layout,
        )

    def _apply(self, selected: str | None) -> None:
        if selected is not None:
            confirm_automatic_vehicle(self._hass, self._entry, selected)
        updated = activate_profile(self._entry.options, selected)
        if updated != dict(self._entry.options):
            self._hass.config_entries.async_update_entry(self._entry, options=updated)

    def _cancel_pending(self) -> None:
        self._generation += 1
        if self._pending is not None:
            self._pending()
            self._pending = None
        self._candidate = None

    @callback
    def stop(self) -> None:
        """Detach before unloading goals; stale callbacks cannot reselect a car."""
        self._stopped = True
        self._cancel_pending()
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()


def prepare_vehicle_selection(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
    """Recheck persisted automatic selection before restoring any vehicle goal."""
    if entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True:
        detector = VehicleDetectionObserver(hass, entry)
        active = entry.options.get(CONF_ACTIVE_VEHICLE_PROFILE)
        if active != detector.effective(active):
            updated = activate_profile(entry.options, None)
            if updated != dict(entry.options):
                hass.config_entries.async_update_entry(entry, options=updated)


def automatic_selection_available(options: Mapping[str, Any]) -> bool:
    """Offer automatic mode only after at least one explicit source selection."""
    return any(profile.detection is not None for profile in profiles(options).values())


def vehicle_selection_matches(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry, expected: str | None,
) -> bool:
    """Fence old profile goals after any setup operation that yields control.

    An unselected runtime is safe to start and must be allowed to finish so its
    detector can settle a new candidate. Only an existing selection can revive
    a stored vehicle goal and therefore requires fresh matching evidence.
    """
    profile = active_profile(entry.options)
    if (profile.id if profile is not None else None) != expected:
        return False
    return (
        expected is None
        or entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is not True
        or VehicleDetectionObserver(hass, entry).effective(expected) == expected
    )
