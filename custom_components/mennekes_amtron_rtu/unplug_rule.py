"""Switch the charging release off whenever a vehicle is unplugged.

Without Virtual Plug & Charge an open release lets any car charge. This rule
closes it after a confirmed disconnect, so the next car needs an explicit
release. With the gate enabled, the gate already owns the release.

A manual pause ends with it: the release now holds the next car, and a
left-over pause would keep the wallbox showing it. A reached target stays.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .device.errors import DeviceError
from .device.registers import CHARGING_RELEASE
from .device.vehicle import snapshot_vehicle_connected

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

CONF_RELEASE_OFF_ON_UNPLUG = "release_off_on_unplug"

_LOGGER = logging.getLogger(__name__)


class UnplugRule:
    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = entry.runtime_data
        self._connected = self._vehicle_connected()
        self._unsubscribe: CALLBACK_TYPE | None = (
            self._runtime.coordinator.async_add_listener(self._observe)
        )

    def stop(self) -> None:
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
        control = self._runtime.control
        if unplugged and not control.read_only and control.plug_charge.get("enabled") is not True:
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
            # switches nothing and waits for no state-change interval.
            if control.pause_requested is True and not control.energy_goal_stop_requested:
                await control.async_resume_charging()
        except (HomeAssistantError, DeviceError, OSError) as err:
            _LOGGER.warning("Control: charging release not switched off after unplugging (%s)", err)
