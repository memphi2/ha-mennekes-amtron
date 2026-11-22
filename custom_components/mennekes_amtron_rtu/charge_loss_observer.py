"""Associate optional vehicle energy without polling a car or writing the wallbox.

The observer only wires things up: it follows the polls, runs one step of
its source at a time and stops with the entry. What is measured lives in the
source (a plug cycle against a value of the car, or the car's own charging
record), what was measured in the ledger.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .charge_loss_const import LOSS_SOURCE_VEHICLE_HISTORY
from .charge_watch import loss_vehicle_options
from .device.charge_loss import LossStatistics
from .exceptions import service_validation_error
from .loss_ledger import LossLedger
from .loss_plug_cycle import PlugCycleLoss
from .loss_record import RecordLoss
from .loss_store import LossStore
from .vehicle_profiles import CONF_VEHICLE_PROFILE_ID
from .vehicle_sources import loss_source

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

class ChargeLossObserver:
    """One optional, bounded loss estimate and its durable weighted aggregate."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._coordinator = entry.runtime_data.coordinator
        options = loss_vehicle_options(entry)
        self.mode = loss_source(options)
        identity = entry.runtime_data.identity.serial_number or entry.entry_id
        self.ledger = LossLedger(
            LossStore(hass, entry.entry_id, identity, profile_id=options.get(CONF_VEHICLE_PROFILE_ID)),
            self._coordinator.async_update_listeners,
        )
        self.source: PlugCycleLoss | RecordLoss = (
            RecordLoss(
                hass, entry, self.ledger, options, self._queue,
                lambda: self._updated(initial=True), self._coordinator.async_update_listeners,
            )
            if self.mode == LOSS_SOURCE_VEHICLE_HISTORY
            else PlugCycleLoss(hass, entry, self.ledger, options, self._queue)
        )
        # The source names whose statistics these are; until it can, nothing runs.
        self._ready = self.source.available
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._task: asyncio.Task[None] | None = None
        self._pending = False

    @property
    def statistics(self) -> LossStatistics:
        return self.ledger.statistics

    @property
    def available(self) -> bool:
        # Every estimate is measured against the wallbox's energy meter.
        return (
            self.ledger.saved and self._ready and self.source.available
            and self._coordinator.current_data().energy_observed_at is not None
        )

    @property
    def can_assign(self) -> bool:
        source = self.source
        return isinstance(source, PlugCycleLoss) and self.available and source.can_assign

    async def async_start(self) -> None:
        await self.ledger.load()
        self._ready = self.source.ready()
        self._unsubscribers.append(self._coordinator.async_add_listener(self._updated))
        self._unsubscribers.extend(self.source.subscribe())
        self._updated(initial=True)
        if isinstance(self.source, PlugCycleLoss) and self._task is not None:
            await asyncio.shield(self._task)

    @callback
    def _updated(self, *, initial: bool = False) -> None:
        if not self._ready:
            self._ready = self.source.ready()
            if not self._ready:
                return
            initial = True
        if self.source.wants(initial=initial) or self.ledger.retry_due:
            self._queue()

    @callback
    def _queue(self) -> None:
        if self.ledger.closed or not self.ledger.readable or not self._ready:
            return
        self._pending = True
        if self._task is None or self._task.done():
            self._task = self._hass.async_create_task(self._run(), "amtron-charge-loss")

    async def _run(self) -> None:
        while self._pending and not self.ledger.closed:
            self._pending = False
            ledger = self.ledger
            await self.source.step()
            if not ledger.saved and not ledger.resting:
                # The step had nothing new to book: what the failed save left
                # is written again, or the estimates would stay unavailable.
                async with ledger.lock:
                    if not ledger.closed:
                        await ledger.commit(lambda _statistics: None)

    async def async_assign(self) -> None:
        """Explicitly associate a last-energy value; never change charging state."""
        source = self.source
        if not isinstance(source, PlugCycleLoss):
            raise service_validation_error("loss_unavailable")
        await source.assign(lambda: self.can_assign)

    async def async_stop(self) -> None:
        self.ledger.closed = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self.source.stop()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
