"""Switch the charging release off whenever a vehicle is unplugged.

Without Virtual Plug & Charge an open release lets any car charge. This rule
closes it after a confirmed disconnect, so the next car needs an explicit
release. With the gate enabled, the gate already owns the release.

A manual pause ends with it: the release now holds the next car, and a
left-over pause would keep the wallbox showing it. A reached target stays.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .device.errors import DeviceError
from .device.notice import FailureNotice
from .device.registers import CHARGING_RELEASE
from .device.vehicle import snapshot_vehicle_connected

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

CONF_RELEASE_OFF_ON_UNPLUG = "release_off_on_unplug"

_LOGGER = logging.getLogger(__name__)

_RETRY_SECONDS = 30


class UnplugRule:
    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = entry.runtime_data
        self._connected = self._vehicle_connected()
        # A switch-off that failed is retried while the car stays away.
        self._owed = False
        self._busy = False
        self._retry: CALLBACK_TYPE | None = None
        self._notice = FailureNotice(_LOGGER, "Control")
        self._unsubscribe: CALLBACK_TYPE | None = (
            self._runtime.coordinator.async_add_listener(self._observe)
        )

    def stop(self) -> None:
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
        if connected is not None:
            self._connected = connected
        if connected is True:
            # The car is back: nothing is owed for the charge that ended.
            self._owed = False
            self._cancel_retry()
        if unplugged:
            self._start()

    def _start(self) -> None:
        control = self._runtime.control
        if self._busy or control.read_only or control.plug_charge.get("enabled") is True:
            return
        self._busy = True
        # Tied to the entry: an unload cancels it before it can write.
        self._entry.async_create_background_task(
            self._hass, self._close_release(), "amtron_release_off_on_unplug",
        )

    async def _close_release(self) -> None:
        control = self._runtime.control
        try:
            if self._runtime.coordinator.current_data().get(CHARGING_RELEASE.key) != 0:
                await control.async_set_charging_release(False)
            # Without a vehicle and with the release off, ending the pause
            # lets no current flow.
            if control.pause_requested is True and not control.energy_goal_stop_requested:
                await control.async_resume_charging()
        except (HomeAssistantError, DeviceError, OSError) as err:
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
        if not self._owed:
            return
        if self._vehicle_connected() is False:
            self._start()
        else:
            # Unknown for now; a returned car clears the debt in ``_observe``.
            self._retry = async_call_later(self._hass, _RETRY_SECONDS, self._retry_now)

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry()
            self._retry = None
