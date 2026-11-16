"""Switch the charging release off whenever a vehicle is unplugged.

Without Virtual Plug & Charge an open release lets any car charge. This rule
closes it after a confirmed disconnect, so the next car needs an explicit
release. With the gate enabled, the gate already owns the release.

A manual pause ends with it: the release now holds the next car, and a
left-over pause would keep the wallbox showing it. A reached target stays.

Whether a car was plugged in is remembered across restarts, so an unplug
while Home Assistant was away still closes the release.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .device.errors import DeviceError
from .device.notice import FailureNotice
from .device.registers import CHARGING_RELEASE
from .device.vehicle import snapshot_vehicle_connected
from .goal_store import ModelStore

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

CONF_RELEASE_OFF_ON_UNPLUG = "release_off_on_unplug"

_LOGGER = logging.getLogger(__name__)

_RETRY_SECONDS = 30


class PlugMemory:
    """Whether a car was plugged in when the wallbox was last read."""

    def __init__(self, connected: bool | None = None) -> None:
        self.connected = connected

    def snapshot(self) -> dict[str, Any]:
        return {"connected": self.connected}

    @classmethod
    def restore(cls, raw: object) -> Self:
        if (
            not isinstance(raw, dict) or raw.keys() != {"connected"}
            or raw["connected"] not in (None, True, False)
        ):
            raise ValueError("invalid saved plug state")
        return cls(raw["connected"])


class PlugMemoryStore(ModelStore[PlugMemory]):
    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="plug_memory", model=PlugMemory)


class _NotAwayError(Exception):
    """The car is not known to be away any more."""


class UnplugRule:
    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = entry.runtime_data
        self._store = PlugMemoryStore(
            hass, entry.entry_id, self._runtime.identity.serial_number or "",
        )
        self._memory_notice = FailureNotice(_LOGGER, "Control")
        self._saving = False
        self._connected = self._vehicle_connected()
        # A switch-off that is still to do; retried while the car stays away.
        self._debt = False
        self._busy = False
        self._stopped = False
        self._retry: CALLBACK_TYPE | None = None
        self._notice = FailureNotice(_LOGGER, "Control")
        self._unsubscribe: CALLBACK_TYPE | None = (
            self._runtime.coordinator.async_add_listener(self._observe)
        )

    async def async_start(self) -> None:
        """Act on an unplug that happened while Home Assistant was away."""
        try:
            remembered = (await self._store.async_load()).connected
        except (OSError, ValueError) as err:
            _LOGGER.warning("Control: ignoring the saved plug state (%s)", type(err).__name__)
            remembered = None
        if self._connected is None:
            # Unknown for now: the next reading is compared with the last known.
            self._connected = remembered
        elif remembered is True and self._connected is False:
            self._owed = True
            self._start()
        self._remember()

    @property
    def _owed(self) -> bool:
        return self._debt

    @_owed.setter
    def _owed(self, owed: bool) -> None:
        if owed != self._debt:
            self._debt = owed
            self._remember()

    def _memory(self) -> bool | None:
        # An open switch-off is remembered as a car still plugged in: a
        # restart before it is done finds the unplug again.
        return True if self._debt else self._connected

    def _remember(self) -> None:
        if not self._saving:
            self._saving = True
            self._entry.async_create_background_task(
                self._hass, self._save(), "amtron_plug_memory",
            )

    async def _save(self) -> None:
        try:
            # Whatever changed while a save ran is saved by the next round.
            saved: bool | None = None
            first = True
            while first or saved != self._memory():
                first, saved = False, self._memory()
                try:
                    await self._store.async_save(PlugMemory(saved))
                except (OSError, ValueError) as err:
                    self._memory_notice.failed(
                        "plug state not saved; an unplug during a restart may go unnoticed",
                        type(err).__name__,
                    )
                    return
                self._memory_notice.recovered("plug state saved again")
        finally:
            self._saving = False

    def stop(self) -> None:
        self._stopped = True
        self._cancel_retry()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _vehicle_connected(self) -> bool | None:
        runtime = self._runtime
        if not runtime.coordinator.last_update_success:
            return None
        return snapshot_vehicle_connected(
            runtime.coordinator.current_data().get, runtime.identity.layout_version,
        )

    @callback
    def _observe(self) -> None:
        connected = self._vehicle_connected()
        # An unknown reading never counts as a disconnect.
        unplugged = connected is False and self._connected is True
        if connected is not None and connected is not self._connected:
            self._connected = connected
            if unplugged:
                # Owed from the unplug on, not only once a try has failed.
                self._debt = True
            self._remember()
        if connected is True:
            # The car is back: nothing is owed for the charge that ended.
            self._owed = False
            self._cancel_retry()
        if unplugged:
            self._start()

    def _owe(self) -> None:
        if self._stopped or self._vehicle_connected() is True:
            self._owed = False
            return
        self._owed = True
        if self._retry is None:
            self._retry = async_call_later(self._hass, _RETRY_SECONDS, self._retry_now)

    def _start(self) -> None:
        control = self._runtime.control
        if control.plug_charge.get("enabled") is True:
            # The gate owns the release now, also for what was owed.
            self._owed = False
            self._cancel_retry()
            return
        if self._busy or control.read_only:
            # Not possible right now, but owed: the release must not stay
            # open for the next car.
            self._owe()
            return
        self._busy = True
        # Tied to the entry: an unload cancels it before it can write.
        self._entry.async_create_background_task(
            self._hass, self._close_release(), "amtron_release_off_on_unplug",
        )

    async def _close_release(self) -> None:
        control = self._runtime.control
        try:
            if self._vehicle_connected() is not False:
                # Plugged in again, or unknown: the release of a car that is
                # there is not this rule's to close.
                raise _NotAwayError
            if self._runtime.coordinator.current_data().get(CHARGING_RELEASE.key) != 0:
                await control.async_set_charging_release(False)
            if self._vehicle_connected() is not False:
                # With a car the switch-off only waits in the queue, and
                # ending the pause would replace it.
                raise _NotAwayError
            # Without a vehicle and with the release off, ending the pause
            # lets no current flow.
            if control.pause_requested is True and not control.energy_goal_stop_requested:
                await control.async_resume_charging()
        except _NotAwayError:
            # A car that is back clears the debt in ``_observe``.
            self._owe()
        except (HomeAssistantError, DeviceError, OSError) as err:
            if self._stopped:
                return
            self._owed = True
            self._notice.failed(
                f"charging release not switched off after unplugging; retrying every {_RETRY_SECONDS} seconds",
                type(err).__name__,
            )
            # One retry at a time, on its own clock: an unchanged poll
            # notifies nobody, and a changed one must not add attempts.
            self._cancel_retry()
            self._retry = async_call_later(self._hass, _RETRY_SECONDS, self._retry_now)
        else:
            self._owed = False
            self._notice.recovered("charging release switched off after unplugging")
        finally:
            self._busy = False

    @callback
    def _retry_now(self, _now: datetime) -> None:
        self._retry = None
        if not self._owed or self._stopped:
            return
        if self._vehicle_connected() is True:
            # The car is back, although no changed poll said so yet.
            self._owed = False
            return
        control = self._runtime.control
        if control.plug_charge.get("enabled") is True:
            # The gate owns the release now.
            self._owed = False
        elif self._vehicle_connected() is False and not control.read_only and not self._busy:
            self._start()
        else:
            # Unknown or not writable for now; a returned car clears the debt
            # in ``_observe``.
            self._retry = async_call_later(self._hass, _RETRY_SECONDS, self._retry_now)

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry()
            self._retry = None
