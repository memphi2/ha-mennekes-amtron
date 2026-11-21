"""Bind the selected car to the current plug-in by its own fresh report.

The selection itself never changes here. A car that plugs in without the
selected car reporting itself plugged in afterwards is not that car: the
selected car's rules, i.e. its charge target, departure plan and learned
phases, rest until it confirms.

Which plug-in is running is the service's to say: a binding holds for the
plug cycle the service reports, across restarts of either side, and ends
with it, at an unplug or a gap long enough to hide one.

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
from .device.meter_log import moment
from .device.notice import FailureNotice
from .device.registers import EVSE_STATE, SESSION_ENERGY
from .entry_types import MennekesAmtronConfigEntry
from .model_store import ModelStore, PendingSaves
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


# The rise that counts as charging once a lower report reset the level.
_RISE_AFTER_DROP = 2.0


class _ChargeCheck:
    """The charge level when the confirmed car started drawing energy."""

    def __init__(self, energy: float, soc: float) -> None:
        self.energy = energy
        self.soc = soc
        # A lower report replaced the first level, which may have been old.
        self.lowered = False
        # When the wallbox had delivered enough energy for the level to rise.
        self.due_at: datetime | None = None


type Cycle = tuple[float | None, float | None]


def _cycle(meter: dict[str, Any]) -> Cycle:
    """The service's plug cycle: since when presence is known, and the plug-in.

    The plug-in alone is unknown for a car that was there before presence
    was known; then the time since presence is known tells cycles apart.
    """
    return meter["known_since"], meter["plugged_at"]


def _time(value: object) -> float | None:
    if value is None:
        return None
    try:
        return moment(value)
    except ValueError as err:
        raise ValueError("invalid saved vehicle binding") from err


class VehicleBindingState:
    """The plug cycle this belongs to, the car confirmed in it, and a refusal."""

    def __init__(
        self, cycle: Cycle | None = None, vehicle: str | None = None, refused_at: float | None = None,
    ) -> None:
        self.cycle = cycle
        self.vehicle = vehicle
        # Only a report after this can confirm the car again in this cycle; a
        # restart of Home Assistant reports the car anew, and the charge
        # check then judges it again.
        self.refused_at = refused_at

    def snapshot(self) -> dict[str, Any]:
        return {
            "cycle": list(self.cycle) if self.cycle is not None else None,
            "vehicle": self.vehicle,
            "refused_at": self.refused_at,
        }

    @classmethod
    def restore(cls, raw: object) -> Self:
        if not isinstance(raw, dict) or raw.keys() != {"cycle", "vehicle", "refused_at"}:
            raise ValueError("invalid saved vehicle binding")
        cycle, vehicle = raw["cycle"], raw["vehicle"]
        if vehicle is not None and (not isinstance(vehicle, str) or not 0 < len(vehicle) <= 64):
            raise ValueError("invalid saved vehicle binding")
        if cycle is not None:
            if not isinstance(cycle, list) or len(cycle) != 2:
                raise ValueError("invalid saved vehicle binding")
            cycle = (_time(cycle[0]), _time(cycle[1]))
        return cls(cycle, vehicle, _time(raw["refused_at"]))


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
        self._first_empty: datetime | None = None
        self._stale_notice = FailureNotice(_LOGGER, "Vehicle")
        self._resting: bool | None = None
        # When this process saw the binding confirmed, and its charge check.
        self._confirmed_at: datetime | None = None
        self._check: _ChargeCheck | None = None
        self._plausible = False
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._saves = PendingSaves()

    async def async_start(self) -> None:
        try:
            self._state = await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Vehicle: ignoring saved binding (%s)", err)
        self._unsubs.append(self._evidence.subscribe(self._changed))
        self._unsubs.append(self._entry.runtime_data.coordinator.async_add_listener(self._changed))
        self._update()

    @callback
    def stop(self) -> None:
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        self._listeners.clear()

    async def async_saved(self) -> None:
        """Wait for saves already started, before the entry unloads."""
        await self._saves.async_wait()

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
        """When the service saw the car at the wallbox arrive; None if unknown or without a car."""
        meter = self._meter()
        if meter is None or meter["present"] is not True or meter["plugged_at"] is None:
            return None
        return dt_util.utc_from_timestamp(meter["plugged_at"])

    @property
    def resting(self) -> bool:
        """A car is plugged in, but the selected car has not confirmed it is that car.

        A plain read: the state moves only in the subscribed callbacks.
        """
        return self._resting is True

    @callback
    def refresh(self) -> None:
        """Judge the newly selected car before its rules start."""
        self._update()

    @callback
    def _changed(self, _event: object = None) -> None:
        self._update()

    def _meter(self) -> dict[str, Any] | None:
        coordinator = self._entry.runtime_data.coordinator
        if not coordinator.last_update_success:
            return None
        return coordinator.current_data().charge_meter

    def _update(self) -> bool:
        now = dt_util.utcnow()
        meter = self._meter()
        wallbox = meter["present"] if meter is not None else None
        profile = active_profile(self._entry.options)
        changed = False
        if wallbox is False:
            if self._first_empty is None:
                self._first_empty = now
            if self._state.cycle is not None or self._state.vehicle is not None:
                if self._state.vehicle is not None:
                    _LOGGER.debug("Vehicle binding ended: the wallbox is empty")
                self._state = VehicleBindingState()
                changed = True
            self._confirmed_at = None
            self._warn_stale(profile, now)
        elif wallbox is True and meter is not None:
            self._first_empty = None
            self._warn_stale(profile, now)
            if self._state.cycle != (cycle := _cycle(meter)):
                if self._state.vehicle is not None:
                    _LOGGER.debug("Vehicle binding ended: another plug-in")
                self._state = VehicleBindingState(cycle)
                self._confirmed_at = None
                changed = True
            if (
                profile is not None and profile.detection is not None
                and self._state.vehicle != profile.id and self._fresh(profile.id)
            ):
                _LOGGER.info("Vehicle: %s confirmed at the wallbox; its rules apply", profile.name)
                self._state.vehicle = profile.id
                self._reset_check(now)
                changed = True
            elif profile is not None and profile.detection is not None and self._state.vehicle == profile.id:
                if self._confirmed_at is None:
                    # Confirmed before a restart: only reports from now on count.
                    self._reset_check(now)
                if (reason := self._contradiction(profile, now)) is not None:
                    _LOGGER.warning(
                        "Vehicle: the car at the wallbox does not behave like %s: %s; its charge target "
                        "and departure plan rest until it reports itself plugged in again",
                        profile.name, reason,
                    )
                    self._state.vehicle = None
                    # Only a report after this one can confirm the car again.
                    self._state.refused_at = now.timestamp()
                    self._confirmed_at = None
                    changed = True
        if changed:
            self._saves.start(self._hass, self._async_save(), "amtron_vehicle_binding")
        if wallbox is None and self._resting is not None:
            # An unreadable wallbox neither confirms the car nor releases its rules.
            return self._resting
        watched = wallbox is True and profile is not None and profile.detection is not None
        detected = watched and profile is not None and self._state.vehicle == profile.id
        resting = watched and not detected
        if resting != self._resting:
            if resting and profile is not None:
                _LOGGER.info(
                    "Vehicle: the car at the wallbox is not confirmed as %s; its charge target and "
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
        """The car reported itself plugged in at home during this plug cycle.

        Without a known plug-in, the cycle begins when the service began to
        watch it, at its first start or after a gap it could not bridge: a
        report from before that may be of another plug-in.
        """
        evidence = self._evidence.snapshot(profile_id)
        plugged_at = self.plugged_at
        if evidence.at_home(plugged_at) is not True or evidence.connected_at is None:
            return False
        cycle = self._state.cycle
        if plugged_at is not None:
            floors = [plugged_at - PLUG_WINDOW]
        elif cycle is not None and cycle[0] is not None:
            floors = [dt_util.utc_from_timestamp(cycle[0])]
        else:
            return False
        if self._state.refused_at is not None:
            floors.append(dt_util.utc_from_timestamp(self._state.refused_at))
        return evidence.connected_at >= max(floors)

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
        # After a lower report, one point back up is only jitter.
        if level >= check.soc + (_RISE_AFTER_DROP if check.lowered else 1e-9):
            # The car charges: this confirmation holds for the connection.
            self._plausible = True
            return None
        if level < check.soc:
            # The first level may have been an older report: measure the rise
            # from this one. The energy keeps counting from the start, so a
            # car that only drains still contradicts in time.
            check.soc, check.lowered = level, True
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
        stale = False
        if (
            profile is not None and profile.detection is not None
            and self._first_empty is not None and now - self._first_empty >= STALE_AFTER
        ):
            evidence = self._evidence.snapshot(profile.id)
            stale = (
                evidence.connected is True and evidence.home is True
                and evidence.connected_at is not None
                and evidence.connected_at - self._first_empty >= STALE_AFTER
            )
        if stale and profile is not None and self._first_empty is not None:
            self._stale_notice.failed(
                f"{profile.name} still reports itself plugged in at home although the wallbox "
                "is empty; its plug signal looks stale",
            )
        else:
            self._stale_notice.recovered("plug signal agrees with the wallbox again")

    async def _async_save(self) -> None:
        try:
            state = self._state
            await self._store.async_save(VehicleBindingState(state.cycle, state.vehicle, state.refused_at))
        except OSError as err:
            _LOGGER.warning("Vehicle: binding not saved (%s)", err)
