"""Bind the selected car to the current plug-in by its own fresh report.

The selection itself never changes here. A car that plugs in without the
selected car reporting itself plugged in afterwards is not that car: the
selected car's rules, i.e. its charge target, departure plan and learned
phases, rest until it confirms. An empty wallbox ends every binding.

A confirmation can still be wrong when a car keeps reporting itself plugged in
after it left. While charging, the car must then contradict itself: it reports
itself unplugged afresh, or its charge level does not rise although the
wallbox delivered several kWh. Missing data never revokes a confirmation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Self

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .device.enums import EvseState
from .device.registers import CP_STATE, EVSE_STATE, SESSION_ENERGY
from .device.vehicle import vehicle_connected
from .entry_types import MennekesAmtronConfigEntry
from .goal_store import ModelStore
from .vehicle_detection import vehicle_evidence
from .vehicle_profiles import (
    CONF_VEHICLE_SOC_USE_PROFILE,
    VehicleProfile,
    active_profile,
    vehicle_options,
)
from .vehicle_soc import vehicle_percentage
from .vehicle_soc_const import CONF_VEHICLE_SOC_ENTITY
from .vehicle_sources import PLUG_WINDOW, profile_entities

_LOGGER = logging.getLogger(__name__)
# A car's cloud data may lag behind the unplug. Only a report this long after
# it that still claims a plugged-in car at home is a stale signal.
STALE_AFTER = timedelta(minutes=10)
# The wallbox notices a real unplug within a poll; an unplug report older than
# this with a car still at the wallbox is about another car.
UNPLUG_GRACE = timedelta(minutes=2)
# Several percent for any car battery, even with charging losses.
PLAUSIBLE_ENERGY_KWH = 4.0
# A full battery may draw energy for climate or balancing without rising.
PLAUSIBLE_BELOW_PERCENT = 95.0


class _ChargeCheck:
    """The charge level when the confirmed car started drawing energy."""

    def __init__(self, energy: float, soc: float) -> None:
        self.energy = energy
        self.soc = soc
        # When the wallbox had delivered enough energy for the level to rise.
        self.due_at: datetime | None = None


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
        self._evidence = vehicle_evidence(hass, entry)
        runtime = entry.runtime_data
        self._store = VehicleBindingStore(hass, entry.entry_id, runtime.identity.serial_number or "")
        self._state = VehicleBindingState()
        # The latest empty poll; the persisted time is updated at plug-in.
        self._last_empty: datetime | None = None
        self._first_empty: datetime | None = None
        self._stale_warned = False
        self._resting: bool | None = None
        # When this process saw the binding confirmed, and its charge check.
        self._confirmed_at: datetime | None = None
        self._check: _ChargeCheck | None = None
        self._plausible = False
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
    def plugged_at(self) -> datetime | None:
        """About when the car at the wallbox arrived; None without a car."""
        return self._state.absent_at if self._wallbox_connected() is True else None

    @property
    def resting(self) -> bool:
        """A car is plugged in, but the selected car has not confirmed it is that car.

        A plain read: the state moves only in the subscribed callbacks.
        """
        return self._resting is True

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
            self._confirmed_at = None
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
                self._reset_check(now)
                changed = True
            elif profile is not None and profile.detection is not None and self._state.vehicle == profile.id:
                if self._confirmed_at is None:
                    # Confirmed before a restart: only reports from now on count.
                    self._reset_check(now)
                if (reason := self._contradiction(profile, now)) is not None:
                    _LOGGER.warning(
                        "The car at the wallbox does not behave like %s: %s; its charge target "
                        "and departure plan rest until it reports itself plugged in again",
                        profile.name, reason,
                    )
                    self._state.vehicle = None
                    # Only a report after this one can confirm the car again;
                    # the comparison allows PLUG_WINDOW before this time.
                    self._state.absent_at = now + PLUG_WINDOW
                    self._confirmed_at = None
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
        absent_at = self._state.absent_at
        if evidence.at_home(absent_at) is not True or evidence.connected_at is None:
            return False
        return absent_at is None or evidence.connected_at >= absent_at - PLUG_WINDOW

    def _reset_check(self, now: datetime) -> None:
        self._confirmed_at = now
        self._check = None
        self._plausible = False

    def _contradiction(self, profile: VehicleProfile, now: datetime) -> str | None:
        """Name a fresh report that contradicts the confirmed car, if any."""
        evidence = self._evidence.snapshot(profile.id)
        confirmed_at = self._confirmed_at
        if (
            evidence.connected is False and evidence.connected_at is not None
            and confirmed_at is not None and evidence.connected_at > confirmed_at
            and now - evidence.connected_at >= UNPLUG_GRACE
        ):
            return "it reports itself unplugged"
        if self._plausible:
            return None
        data = self._entry.runtime_data.coordinator.current_data()
        energy = data.get(SESSION_ENERGY.key)
        soc = self._soc(profile)
        if data.get(EVSE_STATE.key) != EvseState.CHARGING or not isinstance(energy, (int, float)) or soc is None:
            return None
        level, reported = soc
        check = self._check
        if check is None or energy < check.energy:
            # A new charge, or the session counter started over.
            if level < PLAUSIBLE_BELOW_PERCENT:
                self._check = _ChargeCheck(float(energy), level)
            return None
        if level > check.soc:
            # The car charges: this confirmation holds for the connection.
            self._plausible = True
            return None
        if check.due_at is None:
            if energy - check.energy >= PLAUSIBLE_ENERGY_KWH:
                check.due_at = now
            return None
        if reported is not None and reported > check.due_at:
            return f"its charge level stayed at {level:.0f} % while the wallbox delivered {energy - check.energy:.1f} kWh"
        return None

    def _soc(self, profile: VehicleProfile) -> tuple[float, datetime | None] | None:
        """The selected car's charge level and when it was reported."""
        options = vehicle_options(self._entry.options)
        if options.get(CONF_VEHICLE_SOC_USE_PROFILE) is True:
            entity = profile_entities(self._hass, profile.provider_id, profile.device_id).get("soc")
        else:
            entity = options.get(CONF_VEHICLE_SOC_ENTITY)
        state = self._hass.states.get(entity) if isinstance(entity, str) else None
        level = vehicle_percentage(state)
        if state is None or level is None:
            return None
        return level, state.last_reported

    def _warn_stale(self, profile: VehicleProfile | None, now: datetime) -> None:
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
