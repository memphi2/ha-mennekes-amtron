"""Optional local cable/GPS hints from BavarianData's public HA entities.

The upstream tracker can restore old coordinates without exposing their origin.
HA report freshness therefore bounds this heuristic, not the age of the GPS fix.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.zone import in_zone
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu.vehicle_sources.types import (
    DETECTION_MAX_AGE_SECONDS,
    Capability,
    VehicleDetection,
)

from .identity import (
    DOMAIN,
    BavarianVehicleBinding,
    bavarian_vehicle_vin,
)

PLUG_DESCRIPTOR = "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged"
# BavarianData's standard plug sensor, derived from the fields below. From
# BavarianData 0.9.30 it names BMW's time for its state and marks a state
# restored after a restart; an older one has neither and is not used.
CABLE_SENSOR = "charging_cable"
# Cars differ in which plug field they stream, so the most direct one wins: an
# i4 or i5 streams only the port status. BavarianData's evcc bridge reads the
# same fields; the DC-only and non-streamed port fields say nothing here.
PLUG_SOURCES = (
    ("binary_sensor", PLUG_DESCRIPTOR),
    ("binary_sensor", CABLE_SENSOR),
    ("sensor", "vehicle.body.chargingPort.status"),
    ("sensor", "vehicle.body.chargingPort.statusClearText"),
)
_PLUG_DESCRIPTORS = frozenset(descriptor for _domain, descriptor in PLUG_SOURCES)
_PLUG_STATES = {"on": True, "off": False, "connected": True, "disconnected": False}
HOME_ENTITY = "zone.home"
_TRACKER = ("device_tracker", "tracker")


def _entity(hass: HomeAssistant, device_id: str, vin: str, domain: str, suffix: str) -> str | None:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, f"{vin}_{suffix}")
    entry = registry.async_get(entity_id) if entity_id is not None else None
    if entry is None or entry.device_id != device_id or entry.disabled_by is not None:
        return None
    return entity_id


def _usable(hass: HomeAssistant, entity_id: str, suffix: str) -> bool:
    if suffix != CABLE_SENSOR:
        return True
    state = hass.states.get(entity_id)
    return state is not None and "source" in state.attributes


def _entities(hass: HomeAssistant, device_id: str, vin: str) -> tuple[str | None, str | None]:
    plug = next(
        (
            entity for domain, suffix in PLUG_SOURCES
            if (entity := _entity(hass, device_id, vin, domain, suffix)) is not None
            and _usable(hass, entity, suffix)
        ),
        None,
    )
    return plug, _entity(hass, device_id, vin, *_TRACKER)


def detection_entities(hass: HomeAssistant, device_id: str) -> tuple[str | None, str | None]:
    """The enabled plug and location entities, for display."""
    vin = bavarian_vehicle_vin(hass, device_id)
    if vin is None:
        return None, None
    return _entities(hass, device_id, vin)


def detection_capabilities(hass: HomeAssistant, device_id: str) -> frozenset[Capability]:
    """Offer the optional source only when both enabled vehicle entities exist."""
    vin = bavarian_vehicle_vin(hass, device_id)
    if vin is None or not all(_entities(hass, device_id, vin)):
        return frozenset()
    return frozenset({"detection"})


@dataclass(frozen=True, slots=True)
class _Observation:
    state: State | None
    reported: datetime | None


def _observation(state: State | None) -> _Observation:
    return _Observation(state, state.last_reported if state is not None else None)


def _fresh(observation: _Observation, now: datetime) -> State | None:
    state, reported = observation.state, observation.reported
    if (
        state is None or reported is None
        or state.state in ("unknown", "unavailable")
        or state.attributes.get("restored") is True
        or not 0 <= (now - reported).total_seconds() <= DETECTION_MAX_AGE_SECONDS
    ):
        return None
    return state


def _connected(observation: _Observation, now: datetime) -> bool | None:
    state = _fresh(observation, now)
    if state is None or state.attributes.get("descriptor") not in _PLUG_DESCRIPTORS:
        return None
    # The actual BMW descriptor timestamp, when exposed, can be older than the
    # HA report. Do not let an unrelated entity write renew this cable evidence.
    timestamp = state.attributes.get("timestamp")
    if timestamp is None and state.attributes["descriptor"] == CABLE_SENSOR:
        # Without BMW's time, the cable sensor's state proves no plug-in.
        return None
    if timestamp is not None:
        stamp = dt_util.parse_datetime(timestamp) if isinstance(timestamp, str) else None
        if stamp is None or stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= DETECTION_MAX_AGE_SECONDS:
            return None
    # A restored port status may come back in lower or upper case.
    return _PLUG_STATES.get(state.state.strip().lower())


def _number(value: object, minimum: float, maximum: float = math.inf) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) and minimum <= result <= maximum else None


def _home(observation: _Observation, zone: State | None, now: datetime) -> bool | None:
    state = _fresh(observation, now)
    if (
        state is None or state.attributes.get("source_type") != "gps"
        or zone is None or zone.state in ("unknown", "unavailable")
        or zone.attributes.get("restored") is True or zone.attributes.get("passive") is True
    ):
        return None
    latitude = _number(state.attributes.get("latitude"), -90, 90)
    longitude = _number(state.attributes.get("longitude"), -180, 180)
    accuracy = _number(state.attributes.get("gps_accuracy", 0), 0)
    if (
        latitude is None or longitude is None or accuracy is None
        or _number(zone.attributes.get("latitude"), -90, 90) is None
        or _number(zone.attributes.get("longitude"), -180, 180) is None
        or _number(zone.attributes.get("radius"), 0) is None
    ):
        return None
    # A GPS uncertainty circle crossing the home boundary proves neither home
    # nor away. The HA helper uses the configured Home radius in metres.
    if in_zone(zone, latitude, longitude, -accuracy):
        return True
    return None if in_zone(zone, latitude, longitude, accuracy) else False


class BavarianDetectionSource(BavarianVehicleBinding):
    """Keep one vehicle's cable and GPS hints separate from charging status."""

    def _bindings(self) -> tuple[str | None, ...]:
        if not self.available or self._vin is None:
            return (None, None)
        return _entities(self._hass, self._device_id, self._vin)

    def _snapshot(self, states: list[_Observation], zone: State | None) -> VehicleDetection:
        if not self.available:
            return VehicleDetection()
        now = dt_util.utcnow()
        connected_at = states[0].reported
        state = states[0].state
        if state is not None and "timestamp" in state.attributes:
            timestamp = state.attributes["timestamp"]
            connected_at = dt_util.parse_datetime(timestamp) if isinstance(timestamp, str) else None
        return VehicleDetection(
            _connected(states[0], now), _home(states[1], zone, now), connected_at,
        )

    @property
    def snapshot(self) -> VehicleDetection:
        states = [_observation(self._hass.states.get(entity_id)) if entity_id else _observation(None) for entity_id in self._bindings()]
        return self._snapshot(states, self._hass.states.get(HOME_ENTITY))

    def subscribe(self, on_change: Callable[[VehicleDetection], None]) -> CALLBACK_TYPE:
        """Use bounded listeners and each event's evidence, including reports."""
        bindings = self._bindings()
        states: list[_Observation] = []
        zone: State | None = None
        state_unsubs: list[CALLBACK_TYPE] = []

        @callback
        def observe(event: Event[EventStateChangedData] | Event[EventStateReportedData]) -> None:
            nonlocal zone
            current_bindings = self._bindings()
            new_state = event.data["new_state"]
            reported = event.data.get("last_reported")
            observation = _Observation(
                new_state,
                reported if isinstance(reported, datetime) else new_state.last_updated if new_state is not None else None,
            )
            for index, entity_id in enumerate(bindings):
                if current_bindings[index] != entity_id:
                    states[index] = _observation(None)
                elif entity_id is not None and event.data["entity_id"] == entity_id:
                    states[index] = observation
            if event.data["entity_id"] == HOME_ENTITY:
                zone = new_state
            on_change(self._snapshot(states, zone))

        @callback
        def bind() -> None:
            nonlocal bindings, states, zone
            for unsub in state_unsubs:
                unsub()
            state_unsubs.clear()
            bindings = self._bindings()
            states = [_observation(self._hass.states.get(entity_id)) if entity_id else _observation(None) for entity_id in bindings]
            zone = self._hass.states.get(HOME_ENTITY)
            entity_ids = [entity_id for entity_id in bindings if entity_id is not None] + [HOME_ENTITY]
            state_unsubs.extend((
                async_track_state_change_event(self._hass, entity_ids, observe),
                async_track_state_report_event(self._hass, entity_ids, observe),
            ))

        @callback
        def registry_changed(event: Event[er.EventEntityRegistryUpdatedData] | Event[dr.EventDeviceRegistryUpdatedData]) -> None:
            entity_id = event.data.get("entity_id")
            entry = er.async_get(self._hass).async_get(entity_id) if isinstance(entity_id, str) else None
            if (
                event.data.get("device_id") == self._device_id
                or (entry is not None and entry.device_id == self._device_id)
                or any(binding is not None and binding in (entity_id, event.data.get("old_entity_id")) for binding in bindings)
            ):
                bind()
                on_change(self._snapshot(states, zone))

        bind()
        registry_unsubs = (
            self._hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed),
            self._hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, registry_changed),
        )

        @callback
        def unsubscribe() -> None:
            for unsub in (*state_unsubs, *registry_unsubs):
                unsub()
            state_unsubs.clear()

        return unsubscribe
