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
from .charge_watch import PlugCycle, cycle_around, last_cycle, loss_vehicle_options
from .device.charge_loss import Charge, LossStatistics
from .device.client_errors import AmtronConnectionError
from .device.enums import EvseState
from .device.errors import DeviceError
from .device.meter_log import MAX_INTERPOLATION_SECONDS
from .device.notice import FailureNotice
from .device.persistence import StorageCommitUncertainError, StorageStateUnknownError
from .device.registers import EVSE_STATE
from .device.vehicle import snapshot_vehicle_connected
from .exceptions import service_validation_error
from .goal_store import _finish_atomic
from .loss_store import LossStore
from .vehicle_profiles import (
    CONF_VEHICLE_PROFILE_ID,
)
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
        options = loss_vehicle_options(entry)
        self._profile_id: str | None = options.get(CONF_VEHICLE_PROFILE_ID)
        self._profiled = self._profile_id is not None
        self.mode = loss_source(options)
        self._entity = str(options.get(CONF_VEHICLE_LAST_CHARGE_ENERGY, ""))
        self._auto = options.get(CONF_LOSS_AUTO_MATCH, False)
        self._history_source = resolve_history_source(hass, options) if self.mode == LOSS_SOURCE_VEHICLE_HISTORY else None
        self._source = self._history_source.source_key if self._history_source is not None else f"{self.mode}:{self._entity}"
        self._source_ready = self.mode != LOSS_SOURCE_VEHICLE_HISTORY or (
            self._history_source is not None and self._history_source.available
        )
        self._history_available = (
            self._history_source is not None and self._history_source.available
        )
        identity = entry.runtime_data.identity.serial_number or entry.entry_id
        self._store = LossStore(
            hass, entry.entry_id, identity, profile_id=options.get(CONF_VEHICLE_PROFILE_ID),
        )
        self.statistics = LossStatistics()
        self._charging = False
        self._cycle: PlugCycle | None = None
        self._charge: Charge | None = None
        # A cycle of this car that the meter could not measure.
        self._unmeasured = False
        self._started_at = time.time()
        self._change: tuple[float, float] | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._retry: CALLBACK_TYPE | None = None
        self._attempt = 0
        self._task: asyncio.Task[None] | None = None
        self._pending = False
        self._last_query = float("-inf")
        self._lock = asyncio.Lock()
        self._stopping = False
        self._storage_ok = True
        self._storage_notice = FailureNotice(_LOGGER, "Charging loss")
        self._storage_corrupt = False
        self._last_storage_attempt = float("-inf")

    @property
    def available(self) -> bool:
        # Every estimate is measured against the wallbox's energy meter.
        return (
            self._storage_ok and self._source_ready
            and self._coordinator.current_data().energy_observed_at is not None
            and (self.mode != LOSS_SOURCE_VEHICLE_HISTORY or (
                self._history_source is not None and self._history_source.available
            ))
        )

    @property
    def can_assign(self) -> bool:
        return (
            self.mode == LOSS_SOURCE_SENSOR and self.available
            and self._charge is not None and self._unplugged()
        )

    def _unplugged(self) -> bool:
        data = self._coordinator.current_data()
        return self._coordinator.last_update_success and snapshot_vehicle_connected(data.get, self._entry.runtime_data.identity.layout_version) is False

    async def async_start(self) -> None:
        try:
            self.statistics = await self._store.async_load()
        except ValueError:
            _LOGGER.warning("Charging loss: saved estimates unusable; starting without them")
        except OSError:
            self._storage_ok = False
            self._storage_corrupt = True
            _LOGGER.warning("Charging loss: storage cannot be read; optional loss estimates are unavailable")
        self._source_ready = self._configure_source()
        self._history_available = (
            self._history_source is not None and self._history_source.available
        )
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

    def _configure_source(self) -> bool:
        """Wait for a real vehicle identity before replacing a saved aggregate."""
        if self.mode == LOSS_SOURCE_VEHICLE_HISTORY:
            if self._history_source is None or not self._history_source.available:
                return False
            self._source = self._history_source.source_key
        self.statistics.configure_source(self._source)
        return True

    @callback
    def _vehicle_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = vehicle_energy(event.data["old_state"]), vehicle_energy(event.data["new_state"])
        if self._auto and old is not None and new is not None and new != old:
            self._change = (event.time_fired.timestamp(), new)
            self._queue()

    @callback
    def _history_changed(self) -> None:
        available = self._history_source is not None and self._history_source.available
        changed = available != self._history_available
        self._history_available = available
        if not self._source_ready:
            self._updated(initial=True)
        if changed:
            # Device-registry changes do not necessarily alter the wallbox
            # snapshot, so notify entities even when polling stays identical.
            self._coordinator.async_update_listeners()
        if not available:
            return
        if changed:
            self._last_query = float("-inf")
        if time.monotonic() - self._last_query >= 30:
            self._attempt = 0
            self._queue()

    @callback
    def _updated(self, *, initial: bool = False) -> None:
        if not self._source_ready:
            self._source_ready = self._configure_source()
            if not self._source_ready:
                return
            initial = True
        if self.mode == LOSS_SOURCE_VEHICLE_HISTORY:
            # The vehicle's record follows the stop by a while: retry for it.
            charging = self._coordinator.current_data().get(EVSE_STATE.key) == EvseState.CHARGING
            stopped, self._charging = self._charging and not charging, charging
            if initial or stopped:
                self._attempt = 0
                if self._retry is not None:
                    self._retry()
                    self._retry = None
                self._queue()
                return
        elif (cycle := last_cycle(self._entry)) is not None and cycle != self._cycle:
            self._queue()
            return
        if not self._storage_ok and not self._storage_corrupt and time.monotonic() - self._last_storage_attempt >= 60:
            # A failed save discarded the candidate: form it again.
            self._queue()

    async def _energy(self, start: float, end: float) -> float | None:
        """What the meter delivered in a span, as far as the service knows."""
        try:
            return await self._entry.runtime_data.client.async_energy_between(start, end)
        except (AmtronConnectionError, DeviceError):
            return None

    async def _take_plug_cycle(self) -> None:
        """Take the last plug cycle of this car once the meter covers it."""
        cycle = last_cycle(self._entry)
        if cycle is None or cycle == self._cycle:
            return
        if self._profiled and (cycle.owner is None or cycle.owner.id != self._profile_id):
            # Another car's charge: this profile keeps its last match and average.
            self._cycle, self._charge = cycle, None
            return
        energy = await self._energy(cycle.plugged_at, cycle.unplugged_at)
        if energy is None:
            # The reading after the unplug is still to come; it does not come
            # any more once the meter log cannot place the boundary. The
            # charge before it is then no longer the last one.
            if time.time() - cycle.unplugged_at > MAX_INTERPOLATION_SECONDS:
                self._cycle, self._charge, self._unmeasured = cycle, None, True
            return
        self._cycle = cycle
        if energy > 0:
            self._charge = Charge(cycle.plugged_at, cycle.unplugged_at, energy)

    @callback
    def _queue(self) -> None:
        if (
            self._stopping or self._storage_corrupt or not self._source_ready
        ):
            return
        self._pending = True
        if self._task is None or self._task.done():
            self._task = self._hass.async_create_task(self._run(), "amtron-charge-loss")

    async def _run(self) -> None:
        while self._pending and not self._stopping:
            self._pending = False
            if self.mode == LOSS_SOURCE_VEHICLE_HISTORY:
                await self._measure_record()
                continue
            await self._take_plug_cycle()
            charge = self._charge
            if charge is None:
                if self._unmeasured:
                    async with self._lock:
                        candidate = LossStatistics.restore(self.statistics.snapshot())
                        if not self._stopping and (not candidate.observe_charge(None) or await self._save(candidate)):
                            self._unmeasured = False
                continue
            energy: float | None = None
            change: tuple[float, float] | None = None
            if self._auto and self._change is not None and self._unplugged():
                at, value = self._change
                if self._started_at <= charge.end <= at <= charge.end + LOSS_MATCH_WINDOW_SECONDS:
                    energy = value
                    change = self._change
            async with self._lock:
                if self._stopping:
                    continue
                candidate = LossStatistics.restore(self.statistics.snapshot())
                candidate.observe_charge(charge)
                if energy is not None and self._change is change:
                    candidate.apply(charge, energy, association="changed_value")
                if await self._save(candidate) and change is not None and self._change is change:
                    self._change = None

    async def _measure_record(self) -> None:
        """Compare the vehicle's newest record with the meter over the same span."""
        source = self._history_source
        since = (self._coordinator.current_data().charge_meter or {}).get("first_reading_at")
        self._last_query = time.monotonic()
        record = None
        if source is not None and since is not None:
            try:
                record = await source.async_latest(since=since, until=time.time())
            except (HomeAssistantError, TimeoutError):
                record = None
            except Exception as err:  # noqa: BLE001 - isolate third-party services
                # A third-party history service can fail with its own
                # exception types. Keep the wallbox and its setup usable;
                # the same bounded retry policy applies to this provider.
                # Do not log exception messages that may contain VINs.
                _LOGGER.warning("Charging loss: vehicle charging history failed (%s)", type(err).__name__)
        if record is not None:
            cycle = cycle_around(self._entry, record.start, record.end)
            if cycle is None or (self._profiled and (cycle.owner is None or cycle.owner.id != self._profile_id)):
                # Charged elsewhere, or another car was plugged in here meanwhile.
                return
        wallbox = await self._energy(record.start, record.end) if record is not None else None
        if record is None or wallbox is None or wallbox <= 0:
            # No record yet, or the meter reading after its end is still to come.
            self._schedule_retry()
            return
        session = Charge(record.start, record.end, wallbox)
        async with self._lock:
            if self._stopping:
                return
            candidate = LossStatistics.restore(self.statistics.snapshot())
            candidate.apply(session, record.vehicle_kwh, association="history")
            if not self._storage_ok or candidate.snapshot() != self.statistics.snapshot():
                await self._save(candidate)

    def _schedule_retry(self) -> None:
        if self._retry is not None or self._attempt >= len(_RETRY_DELAYS):
            return
        delay = _RETRY_DELAYS[self._attempt]
        self._attempt += 1

        @callback
        def retry(_: object) -> None:
            self._retry = None
            self._queue()

        self._retry = async_call_later(self._hass, delay, retry)

    async def _save(self, candidate: LossStatistics) -> bool:
        self._last_storage_attempt = time.monotonic()

        # The atomic store completes its write even when its caller is
        # cancelled. Commit the corresponding in-memory snapshot as part of
        # the same protected operation before cancellation reaches the caller.
        await _finish_atomic(asyncio.create_task(self._save_and_publish(candidate)))
        return self._storage_ok

    async def _save_and_publish(self, candidate: LossStatistics) -> None:
        try:
            await self._store.async_save(candidate)
        except (StorageCommitUncertainError, StorageStateUnknownError):
            # The replacement is visible or its state could not be read.
            # Retain the candidate for a retry and make estimates unavailable.
            self.statistics = candidate
            self._storage_notice.failed("estimate not saved; retrying without affecting charging")
            self._storage_ok = False
            try:
                self._coordinator.async_update_listeners()
            except Exception:  # noqa: BLE001 - preserve the storage failure
                _LOGGER.error("Charging loss: unable to publish an uncertain update")
            return
        except (OSError, ValueError):
            self._storage_notice.failed("estimate not saved; retrying without affecting charging")
            changed = self._storage_ok
            self._storage_ok = False
        else:
            changed = not self._storage_ok or candidate.snapshot() != self.statistics.snapshot()
            self.statistics = candidate
            self._storage_ok = True
            self._storage_notice.recovered("estimate saved again")
        if changed:
            self._coordinator.async_update_listeners()

    async def async_assign(self) -> None:
        """Explicitly associate a last-energy value; never change charging state."""
        async with self._lock:
            if self._stopping or not self.can_assign or self._charge is None:
                raise service_validation_error("loss_unavailable")
            energy = vehicle_energy(self._hass.states.get(self._entity))
            if energy is None or energy > self._charge.wallbox_kwh:
                raise service_validation_error("loss_energy_invalid")
            # The explicit assignment supersedes older buffered estimates.
            # Events arriving during the save form a new candidate and survive.
            self._change = None
            candidate = LossStatistics.restore(self.statistics.snapshot())
            candidate.apply(self._charge, energy, association="manual")
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
