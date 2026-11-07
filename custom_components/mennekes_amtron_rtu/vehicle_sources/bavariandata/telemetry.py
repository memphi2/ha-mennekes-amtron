"""Bind local BavarianData entities by their public descriptor identities.

Verified against JustChr/BavarianData's entity.py and catalogue.json. The
maxEnergy descriptor is usable energy of the full battery under current
conditions. batterySizeMax is an installed size and is deliberately not used.
"""

from __future__ import annotations

import math
from collections.abc import Callable

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

from custom_components.mennekes_amtron_rtu.vehicle_sources.types import (
    Capability,
    VehicleTelemetry,
)

from .history import (
    HISTORY_DOMAIN,
    bavarian_vehicle_vin,
    bound_vehicle_vin,
    vehicle_source_key,
)

SOC_DESCRIPTORS = (
    "vehicle.drivetrain.batteryManagement.header",
    "vehicle.powertrain.electric.battery.stateOfCharge.displayed",
)
LIMIT_DESCRIPTOR = "vehicle.powertrain.electric.battery.stateOfCharge.target"
CAPACITY_DESCRIPTOR = "vehicle.drivetrain.batteryManagement.maxEnergy"
_DESCRIPTORS = (SOC_DESCRIPTORS, (LIMIT_DESCRIPTOR,), (CAPACITY_DESCRIPTOR,))
_CAPABILITIES: tuple[Capability, ...] = ("soc", "charge_limit", "capacity")


def _entity(hass: HomeAssistant, device_id: str, vin: str, descriptors: tuple[str, ...]) -> tuple[str, str] | None:
    registry = er.async_get(hass)
    for descriptor in descriptors:
        entity_id = registry.async_get_entity_id("sensor", HISTORY_DOMAIN, f"{vin}_{descriptor}")
        if entity_id is None:
            continue
        entry = registry.async_get(entity_id)
        if entry is not None and entry.device_id == device_id and entry.disabled_by is None:
            return entity_id, descriptor
    return None


def telemetry_capabilities(hass: HomeAssistant, device_id: str) -> frozenset[Capability]:
    """A registered, enabled source can be configured while its value is missing."""
    vin = bavarian_vehicle_vin(hass, device_id)
    if vin is None:
        return frozenset()
    return frozenset(
        capability for capability, descriptors in zip(_CAPABILITIES, _DESCRIPTORS, strict=True)
        if _entity(hass, device_id, vin, descriptors) is not None
    )


def _value(state: State | None, descriptor: str) -> float | None:
    if state is None or state.attributes.get("restored") is True or state.attributes.get("descriptor") != descriptor:
        return None
    unit = state.attributes.get("unit_of_measurement")
    capacity = descriptor == CAPACITY_DESCRIPTOR
    if unit not in (("kWh", "Wh") if capacity else ("%",)):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    if capacity and unit == "Wh":
        value /= 1000
    lower, upper = (1, 250) if capacity else (0, 100)
    if not math.isfinite(value) or not lower <= value <= upper or (descriptor == LIMIT_DESCRIPTOR and value == 0):
        return None
    return value


class BavarianTelemetrySource:
    """Keep one vehicle binding and event snapshots across entity renames."""

    def __init__(self, hass: HomeAssistant, device_id: str, *, expected_source_key: str | None = None) -> None:
        self._hass = hass
        self._device_id = device_id
        self._expected_source_key = expected_source_key
        self._vin = bound_vehicle_vin(hass, device_id, expected_source_key)

    @property
    def source_key(self) -> str:
        return self._expected_source_key or vehicle_source_key(self._device_id, self._vin)

    @property
    def available(self) -> bool:
        current = bound_vehicle_vin(self._hass, self._device_id, self._expected_source_key)
        if self._vin is None:
            self._vin = current
        return self._vin is not None and current == self._vin

    def _bindings(self) -> tuple[tuple[str, str] | None, ...]:
        if not self.available or self._vin is None:
            return (None, None, None)
        return tuple(_entity(self._hass, self._device_id, self._vin, descriptors) for descriptors in _DESCRIPTORS)

    @property
    def snapshot(self) -> VehicleTelemetry:
        values = [
            _value(self._hass.states.get(binding[0]), binding[1]) if binding is not None else None
            for binding in self._bindings()
        ]
        return VehicleTelemetry(*values)

    def subscribe(self, on_change: Callable[[VehicleTelemetry], None]) -> CALLBACK_TYPE:
        """Each consumer owns bounded listeners and its own chronological cache."""
        bindings = self._bindings()
        values: list[float | None] = []
        state_unsubs: list[CALLBACK_TYPE] = []

        @callback
        def observe(event: Event[EventStateChangedData] | Event[EventStateReportedData]) -> None:
            if not self.available:
                on_change(VehicleTelemetry())
                return
            current_bindings = self._bindings()
            for index, binding in enumerate(bindings):
                if current_bindings[index] != binding:
                    values[index] = None
                    continue
                if binding is not None and event.data["entity_id"] == binding[0]:
                    # Other fields retain their preceding event values. Reading
                    # global state here would lose a queued 85 -> 50 transition.
                    values[index] = _value(event.data["new_state"], binding[1])
            on_change(VehicleTelemetry(*values))

        @callback
        def bind() -> None:
            nonlocal bindings, values
            for unsub in state_unsubs:
                unsub()
            state_unsubs.clear()
            bindings = self._bindings()
            values = [
                _value(self._hass.states.get(binding[0]), binding[1]) if binding is not None else None
                for binding in bindings
            ]
            entity_ids = [binding[0] for binding in bindings if binding is not None]
            if entity_ids:
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
                or any(binding is not None and binding[0] in (entity_id, event.data.get("old_entity_id")) for binding in bindings)
            ):
                bind()
                on_change(VehicleTelemetry(*values))

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
