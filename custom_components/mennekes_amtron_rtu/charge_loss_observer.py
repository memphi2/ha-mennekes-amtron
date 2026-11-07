"""Associate optional vehicle energy without polling a car or writing the wallbox."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .charge_loss_const import (
    CONF_LOSS_AUTO_MATCH,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    LOSS_MATCH_WINDOW_SECONDS,
    LOSS_SOURCE_SENSOR,
    LOSS_SOURCE_VEHICLE_HISTORY,
)
from .device.charge_loss import LossStatistics
from .device.charging_session import CompletedSession
from .device.registers import CP_STATE, EVSE_STATE
from .device.vehicle import vehicle_connected
from .exceptions import service_validation_error
from .loss_store import LossStore
from .vehicle_sources import loss_source, resolve_history_source

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)
_RETRY_DELAYS = (60, 300, 1800)


def vehicle_energy(state: State | None) -> float | None:
    """Only a reported, finite battery energy with an explicit unit is usable."""
    if state is None or state.attributes.get("restored") is True:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit not in ("kWh", "Wh"):
        return None
    try:
        value = float(state.state) / (1000 if unit == "Wh" else 1)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


class ChargeLossObserver:
    """One optional, bounded session matcher and one durable weighted aggregate."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = entry.runtime_data.coordinator
        self.mode = loss_source(entry.options)
        self._entity = str(entry.options.get(CONF_VEHICLE_LAST_CHARGE_ENERGY, ""))
        self._auto = entry.options.get(CONF_LOSS_AUTO_MATCH, False)
        self._history_source = resolve_history_source(hass, entry.options) if self.mode == LOSS_SOURCE_VEHICLE_HISTORY else None
        self._source = self._history_source.source_key if self._history_source is not None else f"{self.mode}:{self._entity}"
        identity = entry.runtime_data.identity.serial_number or entry.entry_id
        self._store = LossStore(hass, entry.entry_id, identity)
        self.statistics = LossStatistics()
        self._session: CompletedSession | None = None
        self._started_at = time.time()
        self._change: tuple[float, float] | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._retry: CALLBACK_TYPE | None = None
        self._attempt = 0
        self._task: asyncio.Task[None] | None = None
        self._pending = False
        self._query_history = False
        self._last_query = float("-inf")
        self._lock = asyncio.Lock()
        self._stopping = False
        self._storage_ok = True
        self._storage_corrupt = False
        self._last_storage_attempt = float("-inf")

    @property
    def available(self) -> bool:
        return (
            self._storage_ok and self._coordinator.current_data().session_summary_supported
            and (self.mode != LOSS_SOURCE_VEHICLE_HISTORY or (
                self._history_source is not None and self._history_source.available
            ))
        )

    @property
    def can_assign(self) -> bool:
        return (
            self.mode == LOSS_SOURCE_SENSOR and self.available
            and self._session is not None and self._unplugged()
        )

    def _unplugged(self) -> bool:
        data = self._coordinator.current_data()
        return self._coordinator.last_update_success and vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=self._entry.runtime_data.identity.layout_version >= CP_STATE.min_layout,
        ) is False

    async def async_start(self) -> None:
        try:
            self.statistics = await self._store.async_load()
        except (ValueError, OSError):
            self._storage_ok = False
            self._storage_corrupt = True
            _LOGGER.warning("Charging-loss storage cannot be read; optional loss estimates are unavailable")
        self.statistics.configure_source(self._source)
        self._unsubscribers.append(self._coordinator.async_add_listener(self._updated))
        if self.mode == LOSS_SOURCE_SENSOR and self._entity:
            self._unsubscribers.append(async_track_state_change_event(
                self._hass, [self._entity], self._vehicle_changed,
            ))
        elif self._history_source is not None:
            self._unsubscribers.append(self._history_source.subscribe(self._history_changed))
        self._updated(initial=True)
        if self._task is not None:
            await asyncio.shield(self._task)

    @callback
    def _vehicle_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = vehicle_energy(event.data["old_state"]), vehicle_energy(event.data["new_state"])
        if self._auto and old is not None and new is not None and new != old:
            self._change = (event.time_fired.timestamp(), new)
            self._queue()

    @callback
    def _history_changed(self) -> None:
        if time.monotonic() - self._last_query >= 30:
            self._query_history = True
            self._queue()

    @callback
    def _updated(self, *, initial: bool = False) -> None:
        data = self._coordinator.current_data()
        session = CompletedSession.parse(data.charging_session) if data.charging_session else None
        if session != self._session or initial:
            self._session = session
            self._attempt = 0
            if self._retry is not None:
                self._retry()
                self._retry = None
            self._query_history = self._history_source is not None
            self._queue()
        elif not self._storage_ok and not self._storage_corrupt and time.monotonic() - self._last_storage_attempt >= 60:
            # A failed save discarded the candidate, including its matched
            # history energy. Re-read the local history for the current session
            # before retrying storage; otherwise only the empty match is saved.
            self._query_history = self._history_source is not None
            self._queue()

    @callback
    def _queue(self) -> None:
        if self._stopping or self._storage_corrupt:
            return
        self._pending = True
        if self._task is None or self._task.done():
            self._task = self._hass.async_create_task(self._run(), "amtron-charge-loss")

    async def _run(self) -> None:
        while self._pending and not self._stopping:
            self._pending = False
            session = self._session
            query, self._query_history = self._query_history, False
            energy: float | None = None
            association = "changed_value"
            change: tuple[float, float] | None = None
            if session is not None and query:
                self._last_query = time.monotonic()
                try:
                    match = await self._history_source.async_match(session) if self._history_source is not None else None
                except (HomeAssistantError, TimeoutError):
                    match = None
                energy = match.vehicle_kwh if match is not None else None
                association = "history"
                if match is None and session == self._session:
                    self._schedule_retry()
            elif session is not None and self._auto and self._change is not None and self._unplugged():
                at, value = self._change
                if self._started_at <= session.disconnected_at <= at <= session.disconnected_at + LOSS_MATCH_WINDOW_SECONDS:
                    energy = value
                    change = self._change
            async with self._lock:
                if session != self._session or self._stopping:
                    continue
                candidate = LossStatistics.restore(self.statistics.snapshot())
                candidate.observe_session(session)
                if session is not None and energy is not None and (
                    change is None or self._change is change
                ):
                    candidate.apply(session, energy, association=association)
                if await self._save(candidate) and change is not None and self._change is change:
                    self._change = None

    def _schedule_retry(self) -> None:
        if self._retry is not None or self._attempt >= len(_RETRY_DELAYS):
            return
        delay = _RETRY_DELAYS[self._attempt]
        self._attempt += 1

        @callback
        def retry(_: object) -> None:
            self._retry = None
            self._query_history = True
            self._queue()

        self._retry = async_call_later(self._hass, delay, retry)

    async def _save(self, candidate: LossStatistics) -> bool:
        self._last_storage_attempt = time.monotonic()
        try:
            await self._store.async_save(candidate)
        except (OSError, ValueError):
            if self._storage_ok:
                _LOGGER.warning("Charging-loss estimate could not be saved; retrying without affecting charging")
            changed = self._storage_ok
            self._storage_ok = False
        else:
            changed = not self._storage_ok or candidate.snapshot() != self.statistics.snapshot()
            self.statistics = candidate
            self._storage_ok = True
        if changed:
            self._coordinator.async_update_listeners()
        return self._storage_ok

    async def async_assign(self) -> None:
        """Explicitly associate a last-energy value; never change charging state."""
        async with self._lock:
            if self._stopping or not self.can_assign or self._session is None:
                raise service_validation_error("loss_unavailable")
            energy = vehicle_energy(self._hass.states.get(self._entity))
            if energy is None or energy > self._session.wallbox_kwh:
                raise service_validation_error("loss_energy_invalid")
            # The explicit assignment supersedes older buffered estimates.
            # Events arriving during the save form a new candidate and survive.
            self._change = None
            candidate = LossStatistics.restore(self.statistics.snapshot())
            candidate.apply(self._session, energy, association="manual")
            if not await self._save(candidate):
                raise service_validation_error("loss_storage_unavailable")

    async def async_stop(self) -> None:
        self._stopping = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._retry is not None:
            self._retry()
            self._retry = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
