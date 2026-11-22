"""The loss of a charge the car recorded itself, measured over the same span.

The car's record gives the span and the charge levels at its start and end;
the profile's usable capacity turns the levels into the energy that reached
the battery, and the wallbox meter gives what it delivered meanwhile.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .charge_watch import cycle_around, measurable_since
from .departure_observer import vehicle_capacity
from .device.charge_loss import Charge, LossStatistics
from .device.client_errors import AmtronConnectionError
from .device.enums import EvseState
from .device.errors import DeviceError
from .device.notice import FailureNotice
from .device.registers import EVSE_STATE
from .loss_ledger import LossLedger
from .vehicle_profiles import CONF_VEHICLE_PROFILE_ID
from .vehicle_sources import resolve_history_source, resolve_telemetry_source
from .vehicle_sources.types import ChargeRecord

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)
# The car's record follows the end of a charge by a while, at times by hours:
# asked again 1, 5 and 30 minutes and 2 and 6 hours after a trigger.
_RETRY_DELAYS = (60, 240, 1500, 5400, 14400)
# A change of the car's history is not asked for more often than this.
_QUERY_SECONDS = 30
# The car reports whole percent: below this many points the loss of one
# charge is too uncertain to count.
MIN_SOC_DELTA = 20.0


class RecordLoss:
    """Compares the car's charging records with the wallbox meter.

    The car is asked when a charge stops here or the car is unplugged, and a
    few times over the following hours, never on every poll. Every record not
    yet taken into account is measured, oldest first; one counts only inside
    a plug cycle the service saw, and with vehicle profiles only in a cycle of
    that car.
    """

    def __init__(
        self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry, ledger: LossLedger,
        options: Mapping[str, Any], queue: Callable[[], None], restart: Callable[[], None],
        publish: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._history_notice = FailureNotice(_LOGGER, "Charging loss")
        self._ledger = ledger
        self._queue = queue
        self._restart = restart
        self._publish = publish
        self._options = options
        self._profile_id: str | None = options.get(CONF_VEHICLE_PROFILE_ID)
        self._history = resolve_history_source(hass, options)
        # This car's own capacity, also while another profile is active.
        self._telemetry = resolve_telemetry_source(hass, options)
        self._ready = False
        self._was_available = self.available
        self._charging = False
        self._plugged = False
        self._retry: CALLBACK_TYPE | None = None
        self._attempt = 0
        self.asked_at = float("-inf")

    @property
    def available(self) -> bool:
        return self._history is not None and self._history.available

    def ready(self) -> bool:
        """Wait for a real vehicle identity before replacing a saved aggregate."""
        self._was_available = self.available
        if self._history is None or not self._history.available:
            return False
        # Losses from charge levels start their own average.
        self._ledger.statistics.configure_source(self._history.source_key + ":soc")
        self._ready = True
        return True

    def subscribe(self) -> list[CALLBACK_TYPE]:
        return [] if self._history is None else [self._history.subscribe(self._history_changed)]

    def stop(self) -> None:
        self._forget_retry()

    def _forget_retry(self) -> None:
        if self._retry is not None:
            self._retry()
            self._retry = None

    @callback
    def _history_changed(self) -> None:
        available = self.available
        changed, self._was_available = available != self._was_available, available
        if not self._ready:
            self._restart()
        if changed:
            # Device-registry changes do not necessarily alter the wallbox
            # snapshot, so notify entities even when polling stays identical.
            self._publish()
        if not available:
            return
        if changed:
            self.asked_at = float("-inf")
        if time.monotonic() - self.asked_at >= _QUERY_SECONDS:
            self._attempt = 0
            self._queue()

    def wants(self, *, initial: bool) -> bool:
        """A charge stopped here or the car left: its record is due, now or later."""
        data = self._entry.runtime_data.coordinator.current_data()
        charging = data.get(EVSE_STATE.key) == EvseState.CHARGING
        meter = data.charge_meter
        plugged = meter is not None and meter["plugged_at"] is not None
        stopped, self._charging = self._charging and not charging, charging
        unplugged, self._plugged = self._plugged and not plugged, plugged
        if not (initial or stopped or unplugged):
            return False
        self._attempt = 0
        self._forget_retry()
        return True

    async def step(self) -> None:
        if self._ledger.resting:
            # A save just failed: the car is asked again with the retry.
            return
        # Without a plug cycle seen here, no record of the car can be measured.
        since = measurable_since(self._entry)
        self.asked_at = time.monotonic()
        records = None
        if self._history is not None and since is not None:
            try:
                records = await self._history.async_records(since=since, until=time.time())
            except (HomeAssistantError, TimeoutError):
                records = None
            except Exception as err:  # noqa: BLE001 - isolate third-party services
                # A third-party history service can fail with its own
                # exception types. Keep the wallbox and its setup usable;
                # the same bounded retry policy applies to this provider.
                # Do not log exception messages that may contain VINs.
                self._history_notice.failed("vehicle charging history failed", type(err).__name__)
            else:
                if records is not None:
                    self._history_notice.recovered("vehicle charging history available again")
        for record in records or ():
            statistics = self._ledger.statistics
            accounted = statistics.accounted_end
            if accounted is not None and record.end < accounted:
                continue
            cycle = cycle_around(self._entry, record.start, record.end)
            if cycle is None or (
                self._profile_id is not None and (cycle.owner is None or cycle.owner.id != self._profile_id)
            ):
                # Charged elsewhere, or another car was plugged in here meanwhile.
                continue
            charge = statistics.known(record.start, record.end)
            if charge is None:
                try:
                    wallbox = await self._entry.runtime_data.client.async_energy_between(record.start, record.end)
                except (AmtronConnectionError, DeviceError):
                    # The app could not answer: a newer record must not pass
                    # this one by. The retry asks again.
                    break
                if wallbox is None or wallbox <= 0:
                    # Its end is not logged yet, or the log has a gap: a
                    # newer one may still be measurable.
                    continue
                charge = Charge(record.start, record.end, wallbox)
            if not await self._book(charge, record):
                # Not saved: nothing newer may pass it by.
                break
        # The record of the latest charge can follow hours later; later
        # tries also correct a charge whose capacity was not known yet.
        self._schedule_retry()

    async def _book(self, charge: Charge, record: ChargeRecord) -> bool:
        capacity = vehicle_capacity(self._hass, self._telemetry, self._options)
        points = record.soc_end - record.soc_start

        def book(statistics: LossStatistics) -> None:
            if points < MIN_SOC_DELTA:
                statistics.reject(charge, "too_small")
            elif capacity is None:
                statistics.reject(charge, "no_capacity")
            else:
                statistics.apply(charge, points / 100 * capacity, association="history")

        async with self._ledger.lock:
            return not self._ledger.closed and await self._ledger.commit(book)

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
