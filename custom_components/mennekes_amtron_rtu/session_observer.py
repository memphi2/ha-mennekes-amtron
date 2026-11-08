"""Retain one fully observed local charge without affecting device operation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .charge_loss_const import CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED
from .charging_cost_const import CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED
from .coordinator import MennekesAmtronCoordinator
from .device.charging_session import SessionHistory
from .device.data import DeviceIdentity, WallboxData
from .device.registers import CP_STATE, ENERGY_TOTAL
from .goal_store import SessionHistoryStore
from .vehicle_profiles import profiles, vehicle_options

_LOGGER = logging.getLogger(__name__)
_RETRY_SECONDS = 60.0


def session_capture_enabled(entry: ConfigEntry) -> bool:
    """Anchor the meter before detection selects a car with loss estimates."""
    return (
        entry.options.get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED) != COST_SOURCE_DISABLED
        or vehicle_options(entry.options).get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED) != LOSS_SOURCE_DISABLED
        or any(
            profile.options.get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED) != LOSS_SOURCE_DISABLED
            for profile in profiles(entry.options).values()
        )
    )


class SessionObserver:
    """Optional direct-mode session capture, fed before each poll is published.

    The owner calls ``async_setup`` before polling, assigns ``async_observe`` to
    the coordinator callback, and awaits ``async_close`` during unload. Failed
    polls must call ``async_observe(None)`` to invalidate partial measurements.
    No background task or extra bus request is needed. Storage failures are
    retried at most once per minute and never interrupt charging or polling.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, identity: DeviceIdentity,
        coordinator: MennekesAmtronCoordinator, *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._coordinator = coordinator
        self._enabled = (
            session_capture_enabled(entry)
            and identity.layout_version >= ENERGY_TOTAL.min_layout
            and bool(identity.serial_number)
        )
        self._cp_supported = identity.layout_version >= CP_STATE.min_layout
        self._history = SessionHistory(cp_supported=self._cp_supported)
        self._store = SessionHistoryStore(hass, entry.entry_id, identity.serial_number or "")
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._loaded = False
        self._dirty = False
        self._closed = False
        self._retry_at = 0.0
        self._storage_failed = False

    def rebind(self, coordinator: MennekesAmtronCoordinator) -> None:
        """Continue the same observed cycle after a logical profile reload."""
        self._coordinator = coordinator

    async def async_setup(self) -> None:
        """Restore a completed session; optional storage errors remain isolated."""
        async with self._lock:
            if self._enabled and not self._closed and not self._loaded:
                await self._async_load()

    async def _async_load(self) -> None:
        try:
            restored = await self._store.async_load()
        except (OSError, ValueError) as err:
            self._storage_error(err)
        else:
            self._history = SessionHistory.restore(
                restored.snapshot(), cp_supported=self._cp_supported,
            )
            self._loaded = True
            self._storage_failed = False

    def _storage_error(self, err: OSError | ValueError) -> None:
        self._retry_at = self._monotonic() + _RETRY_SECONDS
        if not self._storage_failed:
            _LOGGER.warning(
                "Optional charging-session storage is unavailable (%s); retrying in 60 seconds",
                type(err).__name__,
            )
            self._storage_failed = True

    async def _async_save(self) -> None:
        try:
            await self._store.async_save(self._history)
        except (OSError, ValueError) as err:
            self._storage_error(err)
        else:
            self._dirty = False
            self._storage_failed = False

    async def async_observe(self, data: WallboxData | None) -> None:
        """Capture only complete cycles and publish stable session metadata."""
        async with self._lock:
            if self._closed:
                return
            if self._enabled and not self._loaded and self._monotonic() >= self._retry_at:
                await self._async_load()
            if self._enabled and self._loaded:
                self._dirty = self._history.observe(data, now=self._wall_clock()) or self._dirty
                if self._dirty and self._monotonic() >= self._retry_at:
                    await self._async_save()
            if data is not None:
                data.session_summary_supported = self._enabled and self._loaded
                latest = self._history.latest
                data.charging_session = latest.as_dict() if latest is not None else None

    async def async_close(self) -> None:
        """Finish pending persistence without letting storage break unloading."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._coordinator.observe_session == self.async_observe:
                self._coordinator.observe_session = None
            if self._dirty:
                await self._async_save()
