"""The loss of a charge the car recorded itself, measured over the same span."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .charge_watch import cycle_around, measurable_since
from .device.charge_loss import Charge
from .device.client_errors import AmtronConnectionError
from .device.enums import EvseState
from .device.errors import DeviceError
from .device.registers import EVSE_STATE
from .loss_ledger import LossLedger
from .vehicle_profiles import CONF_VEHICLE_PROFILE_ID
from .vehicle_sources import resolve_history_source

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)
# The car's record follows the end of a charge by a while.
_RETRY_DELAYS = (60, 300, 1800)
# A change of the car's history is not asked for more often than this.
_QUERY_SECONDS = 30


class RecordLoss:
    """Compares the car's newest charging record with the wallbox meter.

    The car is asked when a charge stops here and a few times after that,
    never on every poll. A record counts only inside a plug cycle the service
    saw, and with vehicle profiles only in a cycle of that car.
    """

    def __init__(
        self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry, ledger: LossLedger,
        options: Mapping[str, Any], queue: Callable[[], None], restart: Callable[[], None],
        publish: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._ledger = ledger
        self._queue = queue
        self._restart = restart
        self._publish = publish
        self._profile_id: str | None = options.get(CONF_VEHICLE_PROFILE_ID)
        self._history = resolve_history_source(hass, options)
        self._ready = False
        self._was_available = self.available
        self._charging = False
        self._retry: CALLBACK_TYPE | None = None
        self._attempt = 0
        self.asked_at = float("-inf")

    @property
    def available(self) -> bool:
        return self._history is not None and self._history.available

    def ready(self) -> bool:
        """Wait for a real vehicle identity before replacing a saved aggregate."""
        if self._history is None or not self._history.available:
            return False
        self._ledger.statistics.configure_source(self._history.source_key)
        self._ready = True
        self._was_available = True
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
        """A charge stopped here: its record is due, now or after a while."""
        charging = self._entry.runtime_data.coordinator.current_data().get(EVSE_STATE.key) == EvseState.CHARGING
        stopped, self._charging = self._charging and not charging, charging
        if not (initial or stopped):
            return False
        self._attempt = 0
        self._forget_retry()
        return True

    async def step(self) -> None:
        # Without a plug cycle seen here, no record of the car can be measured.
        since = measurable_since(self._entry)
        self.asked_at = time.monotonic()
        record = None
        if self._history is not None and since is not None:
            try:
                record = await self._history.async_latest(since=since, until=time.time())
            except (HomeAssistantError, TimeoutError):
                record = None
            except Exception as err:  # noqa: BLE001 - isolate third-party services
                # A third-party history service can fail with its own
                # exception types. Keep the wallbox and its setup usable;
                # the same bounded retry policy applies to this provider.
                # Do not log exception messages that may contain VINs.
                _LOGGER.warning("Charging loss: vehicle charging history failed (%s)", type(err).__name__)
        wallbox = None
        if record is not None:
            cycle = cycle_around(self._entry, record.start, record.end)
            if cycle is None or (
                self._profile_id is not None and (cycle.owner is None or cycle.owner.id != self._profile_id)
            ):
                # Charged elsewhere, or another car was plugged in here meanwhile.
                return
            try:
                wallbox = await self._entry.runtime_data.client.async_energy_between(record.start, record.end)
            except (AmtronConnectionError, DeviceError):
                wallbox = None
        if record is None or wallbox is None or wallbox <= 0:
            # No record yet, or the meter reading after its end is still to come.
            self._schedule_retry()
            return
        charge, vehicle_kwh = Charge(record.start, record.end, wallbox), record.vehicle_kwh
        async with self._ledger.lock:
            if not self._ledger.closed:
                await self._ledger.commit(
                    lambda statistics: statistics.apply(charge, vehicle_kwh, association="history"),
                )

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
