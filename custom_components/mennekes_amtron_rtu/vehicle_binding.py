"""Bind the selected car to the current plug-in by its own fresh report.

The selection itself never changes here. A car that plugs in without the
selected car reporting itself plugged in afterwards is not that car: the
selected car's rules, i.e. its charge target, departure plan and learned
phases, rest until it confirms. An empty wallbox ends every binding.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Self

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .device.registers import CP_STATE, EVSE_STATE
from .device.vehicle import vehicle_connected
from .entry_types import MennekesAmtronConfigEntry
from .goal_store import ModelStore
from .vehicle_detection import VehicleEvidence
from .vehicle_profiles import active_profile

_LOGGER = logging.getLogger(__name__)
# A car's cloud data may lag behind the unplug. Only a report this long after
# it that still claims a plugged-in car at home is a stale signal.
STALE_AFTER = timedelta(minutes=10)


class VehicleBindingState:
    """When the wallbox was last seen empty, and which car confirmed since."""

    def __init__(self, absent_at: datetime | None = None, vehicle: str | None = None) -> None:
        self.absent_at = absent_at
        self.vehicle = vehicle

    def snapshot(self) -> dict[str, Any]:
        return {
            "absent_at": self.absent_at.isoformat() if self.absent_at is not None else None,
            "vehicle": self.vehicle,
        }

    @classmethod
    def restore(cls, raw: object) -> Self:
        if not isinstance(raw, dict) or raw.keys() != {"absent_at", "vehicle"}:
            raise ValueError("invalid saved vehicle binding")
        absent_at, vehicle = raw["absent_at"], raw["vehicle"]
        if vehicle is not None and (not isinstance(vehicle, str) or not 0 < len(vehicle) <= 64):
            raise ValueError("invalid saved vehicle binding")
        if absent_at is None:
            return cls(None, vehicle)
        if not isinstance(absent_at, str) or len(absent_at) > 64:
            raise ValueError("invalid saved vehicle binding")
        try:
            parsed = datetime.fromisoformat(absent_at)
        except ValueError as err:
            raise ValueError("invalid saved vehicle binding") from err
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid saved vehicle binding")
        return cls(parsed, vehicle)


class VehicleBindingStore(ModelStore[VehicleBindingState]):
    """Survive restarts, so a confirmed car keeps its rules while plugged in."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="vehicle_binding", model=VehicleBindingState)


class VehicleBinding:
    """Decide whether the selected car's rules apply to the car plugged in."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._evidence = VehicleEvidence(hass, entry)
        runtime = entry.runtime_data
        self._store = VehicleBindingStore(hass, entry.entry_id, runtime.identity.serial_number or "")
        self._state = VehicleBindingState()
        # The latest empty poll; the persisted time is updated at plug-in.
        self._last_empty: datetime | None = None
        self._first_empty: datetime | None = None
        self._stale_warned = False
        self._resting: bool | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    async def async_start(self) -> None:
        try:
            self._state = await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Ignoring saved vehicle binding: %s", err)
        self._unsubs.append(self._evidence.subscribe(self._changed))
        self._unsubs.append(self._entry.runtime_data.coordinator.async_add_listener(self._changed))
        self._update()

    @callback
    def stop(self) -> None:
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        self._listeners.clear()

    @callback
    def subscribe(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call the listener whenever the selected car's rules start or stop resting."""
        self._listeners.append(listener)

        @callback
        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    @property
    def resting(self) -> bool:
        """A car is plugged in, but the selected car has not confirmed it is that car."""
        return self._update()

    @property
    def confirmed(self) -> bool | None:
        """Whether the plugged-in car is the selected one; None without a car or detection."""
        profile = active_profile(self._entry.options)
        if profile is None or profile.detection is None or self._wallbox_connected() is not True:
            return None
        return not self.resting

    @callback
    def _changed(self, _event: object = None) -> None:
        self._update()

    def _wallbox_connected(self) -> bool | None:
        runtime = self._entry.runtime_data
        if not runtime.coordinator.last_update_success:
            return None
        data = runtime.coordinator.current_data()
        return vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=runtime.identity.layout_version >= CP_STATE.min_layout,
        )

    def _update(self) -> bool:
        now = dt_util.utcnow()
        wallbox = self._wallbox_connected()
        profile = active_profile(self._entry.options)
        changed = False
        if wallbox is False:
            self._last_empty = now
            if self._first_empty is None:
                self._first_empty = now
                self._stale_warned = False
            if self._state.vehicle is not None:
                _LOGGER.debug("Vehicle binding ended: the wallbox is empty")
                self._state.vehicle = None
                changed = True
            self._warn_stale(profile, now)
        elif wallbox is True:
            self._first_empty = None
            if self._last_empty is not None:
                self._state.absent_at = self._last_empty
                self._last_empty = None
                changed = True
            if (
                profile is not None and profile.detection is not None
                and self._state.vehicle != profile.id and self._fresh(profile.id)
            ):
                _LOGGER.info("Vehicle %s confirmed at the wallbox; its rules apply", profile.name)
                self._state.vehicle = profile.id
                changed = True
        if changed:
            self._hass.async_create_task(self._async_save(), "amtron_vehicle_binding", eager_start=False)
        if wallbox is None and self._resting is not None:
            # An unreadable wallbox neither confirms the car nor releases its rules.
            return self._resting
        resting = (
            wallbox is True and profile is not None and profile.detection is not None
            and self._state.vehicle != profile.id
        )
        if resting != self._resting:
            if resting and profile is not None:
                _LOGGER.info(
                    "The car at the wallbox is not confirmed as %s; its charge target and "
                    "departure plan rest until it reports itself plugged in", profile.name,
                )
            # Without a car, nothing rests; the start needs no announcement.
            notify = self._resting is not None or resting
            self._resting = resting
            if notify:
                for listener in list(self._listeners):
                    self._hass.loop.call_soon(listener)
        return resting

    def _fresh(self, profile_id: str) -> bool:
        evidence = self._evidence.snapshot(profile_id)
        if evidence.connected is not True or evidence.home is not True or evidence.connected_at is None:
            return False
        absent_at = self._state.absent_at
        return absent_at is None or evidence.connected_at >= absent_at

    def _warn_stale(self, profile: Any, now: datetime) -> None:
        if (
            self._stale_warned or profile is None or profile.detection is None
            or self._first_empty is None or now - self._first_empty < STALE_AFTER
        ):
            return
        evidence = self._evidence.snapshot(profile.id)
        if (
            evidence.connected is True and evidence.home is True and evidence.connected_at is not None
            and evidence.connected_at - self._first_empty >= STALE_AFTER
        ):
            self._stale_warned = True
            _LOGGER.warning(
                "%s still reports itself plugged in at home although the wallbox has been "
                "empty for %.0f minutes; its plug signal looks stale",
                profile.name, (now - self._first_empty).total_seconds() / 60,
            )

    async def _async_save(self) -> None:
        try:
            await self._store.async_save(VehicleBindingState(self._state.absent_at, self._state.vehicle))
        except OSError as err:
            _LOGGER.warning("Vehicle binding not saved: %s", err)
