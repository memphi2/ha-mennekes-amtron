"""Optional local price observations and bounded charging-cost estimates."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)

from .charging_cost_const import (
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_CHARGING_PRICE,
    CONF_CHARGING_PRICE_ENTITY,
    COST_SOURCE_FIXED,
)
from .device.charging_cost import (
    CostAccumulator,
    CostCheckpoint,
    CostResult,
    TariffConfig,
)
from .device.data import WallboxData
from .device.enums import EvseState
from .device.notice import FailureNotice
from .device.registers import ENERGY_TOTAL, EVSE_STATE
from .entry_types import MennekesAmtronConfigEntry
from .model_store import ModelStore
from .network_tariff_lookup import german_network_tariffs

_LOGGER = logging.getLogger(__name__)
PRICE_VALID_SECONDS = 90 * 60
_NOTHING = CostResult(0.0, 0.0, 0.0, True)
_EURO_UNITS = {"€/kWh": 1.0, "ct/kWh": 0.01, "€/MWh": 0.001}
_RETAINED_OPTIONS = (
    CONF_CHARGING_COST_SOURCE, CONF_CHARGING_PRICE, CONF_CHARGING_PRICE_ENTITY,
    CONF_CHARGING_NETWORK_TARIFF,
)


def electricity_price(state: State | None, currency: str) -> float | None:
    """A price per kWh in Home Assistant's currency; negative prices are legitimate."""
    if state is None or state.attributes.get("restored") is True:
        return None
    units = {f"{currency}/kWh": 1.0, f"{currency}/MWh": 0.001, **(_EURO_UNITS if currency == "EUR" else {})}
    factor = units.get(str(state.attributes.get("unit_of_measurement")))
    if factor is None:
        return None
    try:
        price = float(state.state) * factor
    except ValueError:
        return None
    return price if math.isfinite(price) else None


class LastChargingCost:
    """The cost of the last charge; tariff changes never reprice an old charge."""

    def __init__(self, result: CostResult | None = None) -> None:
        self.result = result

    def snapshot(self) -> dict[str, Any]:
        return {"result": self.result.as_dict() if self.result else None}

    @classmethod
    def restore(cls, raw: object) -> LastChargingCost:
        if not isinstance(raw, dict) or raw.keys() != {"result"}:
            raise ValueError("invalid charging cost storage")
        return cls(None if raw["result"] is None else CostResult.parse(raw["result"]))


class ChargingCostObserver:
    """Price only intervals observed by this HA process."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass, self._entry = hass, entry
        self._entity = str(entry.options.get(CONF_CHARGING_PRICE_ENTITY, ""))
        self._fixed = entry.options.get(CONF_CHARGING_COST_SOURCE) == COST_SOURCE_FIXED
        self._tariff = self._network_tariff(entry.options)
        self._configured_options = deepcopy(dict(entry.options))
        self._configured_data = dict(entry.data)
        self._identity = (
            entry.runtime_data.identity.serial_number, entry.runtime_data.identity.layout_version,
        )
        max_gap = 660
        self._accumulator = CostAccumulator(self._tariff, max_gap=max_gap)
        self._store = ModelStore(
            hass, entry.entry_id, entry.runtime_data.identity.serial_number or entry.entry_id,
            kind="charging_cost", model=LastChargingCost,
        )
        self.last = LastChargingCost()
        self._supported = False
        self._active = False  # A car is plugged in: show the running cost.
        self._unplugged = False  # The last known poll saw no car.
        self._cycle_start: CostCheckpoint | None = None
        # The running cost started without an idle reading: never complete.
        self._cycle_partial = False
        # The start of a charge whose car left before its final meter reading.
        self._closing_start: CostCheckpoint | None = None
        self._closing_partial = False
        # Since when the service has known the plug state without a long gap.
        self._known_since: float | None = None
        self._plugged_at: float | None = None
        self._started_at = time.time()
        self._price_at = self._started_at
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._unsubscribe_report: CALLBACK_TYPE | None = None
        self._expiry: CALLBACK_TYPE | None = None
        self._live_price: float | None = None
        self._closed = False
        self._storage_notice = FailureNotice(_LOGGER, "Charging cost")
        self._storage_corrupt = False
        self._dirty = False
        self._retry_at = 0.0

    def _network_tariff(self, options: Mapping[str, Any]) -> TariffConfig:
        """A saved network tariff counts only where it was made for: Germany in euros."""
        if not german_network_tariffs(self._hass.config):
            return TariffConfig.parse({"module": "none"})
        return TariffConfig.parse(options.get(CONF_CHARGING_NETWORK_TARIFF, {"module": "none"}))

    def retain_for_reload(self, entry: MennekesAmtronConfigEntry) -> bool:
        """Keep observed prices and energy only across an unchanged live source.

        Continuity across a reconnect is judged by the presence-gap rule.
        Vehicle-profile changes do not change this wallbox-wide calculation.
        """
        return (
            not self._closed and not self._storage_corrupt
            and entry.entry_id == self._entry.entry_id
            and dict(entry.data) == self._configured_data
            and (
                entry.runtime_data.identity.serial_number,
                entry.runtime_data.identity.layout_version,
            ) == self._identity
            and all(
                entry.options.get(key) == self._configured_options.get(key)
                for key in _RETAINED_OPTIONS
            )
        )

    def rebind(self, entry: MennekesAmtronConfigEntry) -> None:
        """Hand over the live observer without resubscribing or resetting costs."""
        if not self.retain_for_reload(entry):
            raise ValueError("charging-cost source changed during reload")
        self._entry = entry
        self._configured_options = deepcopy(dict(entry.options))

    def apply_tariff_update(self) -> bool:
        """Take the entry's tariff without restarting the Modbus connection.

        The entry's listener calls this when nothing but the tariff, or what
        other owners applied in place, changed. False sends it to a reload.
        """
        current = deepcopy(dict(self._entry.options))
        if self._closed or dict(self._entry.data) != self._configured_data:
            return False
        try:
            tariff = self._network_tariff(current)
        except ValueError:
            return False
        if tariff != self._tariff:
            # A meter interval spanning two tariff definitions cannot be
            # priced with either definition alone; older samples arriving
            # later are not fresh either.
            self._price_at = max(self._price_at, time.time())
            self._accumulator.observe_energy(self._price_at, None)
            self._tariff = tariff
            self._accumulator.tariff = tariff
        self._configured_options = current
        return True

    @property
    def current(self) -> CostResult:
        """The running charge: zero from the plug-in on, zero again without a car."""
        now = self._accumulator.checkpoint()
        if not self._active or now is None or self._cycle_start is None:
            return _NOTHING
        return self._since(now, self._cycle_start, self._cycle_partial)

    @staticmethod
    def _since(end: CostCheckpoint, start: CostCheckpoint, partial: bool) -> CostResult:
        result = end.since(start)
        if partial and result.complete:
            return CostResult(result.known_cost, 0.0, result.energy_kwh, False)
        return result

    def _take_over(self, result: CostResult) -> None:
        """A charge that ended or stopped becomes the last charge."""
        if result.energy_kwh > 0 and result != self.last.result:
            self.last = LastChargingCost(result)
            self._dirty = True

    @property
    def available(self) -> bool:
        return not self._storage_corrupt and self._supported

    def _price(self) -> float | None:
        if self._fixed:
            return float(self._entry.options[CONF_CHARGING_PRICE])
        return self._live_price

    def unit_price(self, at: float) -> float | None:
        """What a kilowatt hour costs at ``at``, network charges included."""
        return self._tariff.price_at(at, self._price())

    async def async_start(self) -> None:
        try:
            self.last = await self._store.async_load()
        except ValueError:
            # An estimate is no safeguard: a saved one that cannot be used is
            # dropped, and the next charge writes a new one.
            _LOGGER.warning("Charging cost: saved estimate unusable; starting without a last charge")
        except OSError:
            self._storage_corrupt = True
            _LOGGER.warning("Charging cost: storage cannot be read; optional cost estimates are unavailable")
        if self._fixed:
            self._accumulator.set_price(self._started_at, self._price())
        if not self._fixed and self._entity:
            self._unsubscribe = async_track_state_change_event(self._hass, [self._entity], self._price_changed)
            self._unsubscribe_report = async_track_state_report_event(self._hass, [self._entity], self._price_changed)
            self._accept_price(self._hass.states.get(self._entity), time.time())

    @callback
    def _price_changed(self, event: Event[EventStateChangedData] | Event[EventStateReportedData]) -> None:
        self._accept_price(event.data["new_state"], event.time_fired.timestamp())

    @callback
    def _accept_price(self, state: State | None, at: float) -> None:
        if self._expiry is not None:
            self._expiry()
            self._expiry = None
        price = electricity_price(state, self._hass.config.currency)
        valid_until = state.last_reported.timestamp() + PRICE_VALID_SECONDS if state is not None else at
        if not at <= valid_until:
            price = None
        if price != self._live_price:
            self._live_price = price
            self._price_at = at
            self._accumulator.set_price(at, price)
        if price is not None:
            @callback
            def expired(_: object) -> None:
                self._expiry = None
                self._live_price = None
                self._price_at = valid_until
                self._accumulator.set_price(valid_until, None)
            self._expiry = async_call_later(self._hass, max(0, valid_until - time.time()), expired)

    async def async_observe(self, data: WallboxData | None) -> None:
        if self._closed or self._storage_corrupt:
            return
        now = time.time()
        if data is None:
            # One failed poll is no meter evidence: the next reading spans the
            # gap, and the service judges how long the plug state was unknown.
            return
        self._supported = data.energy_observed_at is not None
        # The plug state as the service saw it, which runs on without us.
        meter = data.charge_meter or {}
        present = meter.get("present")
        known_since = meter.get("known_since")
        if known_since != self._known_since:
            if self._known_since is not None:
                self._gap_passed()
            self._known_since = known_since
        at = data.energy_observed_at or 0.0
        fresh = data.energy_read and self._started_at <= at <= now + 5 and at >= self._price_at
        energy = data.get(ENERGY_TOTAL.key)
        energy = float(energy) if isinstance(energy, (float, int)) and not isinstance(energy, bool) else None
        charging = data.get(EVSE_STATE.key) == EvseState.CHARGING
        checkpoint = None
        # A failed or rejected read leaves the previous value, which must not
        # pass for a new reading.
        if fresh and energy is not None and self._accumulator.observe_energy(at, energy):
            checkpoint = self._accumulator.checkpoint()
        fresh = checkpoint is not None
        if present is True:
            plugged_at = meter.get("plugged_at")
            if self._active and plugged_at is not None and plugged_at != self._plugged_at:
                # The service saw an unplug and a new plug-in that no poll of
                # ours showed: the earlier charge ends here without its last
                # reading, and this one has no known start.
                self._cycle_partial = True
                self._take_over(self.current)
                self._cycle_start, self._cycle_partial = None, False
            self._plugged_at = plugged_at
            replugged, self._unplugged, self._active = self._unplugged, False, True
            self._closing_start = None
            if self._cycle_start is None and fresh:
                # Right after an unplug and before charging, no energy has
                # flowed since: this reading is the start of the charge.
                self._cycle_start, self._cycle_partial = checkpoint, not (replugged and not charging)
            elif (checkpoint is not None and self._cycle_start is not None and not charging
                  and checkpoint.meter_kwh == self._cycle_start.meter_kwh):
                # Waiting plugged in: nothing has flowed, start from here.
                self._cycle_start = checkpoint
            if not charging:
                # Charging stopped with the car still plugged in.
                self._take_over(self.current)
        elif present is False:
            if self._active:
                # The car left: what is known now is the last charge, and the
                # final meter reading may still follow.
                self._take_over(self.current)
                self._closing_start, self._closing_partial = self._cycle_start, self._cycle_partial
                self._cycle_start, self._cycle_partial = None, False
            if checkpoint is not None:
                if self._closing_start is not None:
                    self._take_over(self._since(checkpoint, self._closing_start, self._closing_partial))
                    self._closing_start = None
                # Only a reading while unplugged starts the next running cost;
                # an unplug without one must not carry this charge into the next.
                self._cycle_start, self._cycle_partial = checkpoint, False
            self._active, self._unplugged = False, True
        if self._dirty and time.monotonic() >= self._retry_at:
            await self._save()

    def _gap_passed(self) -> None:
        """The service lost sight of the plug state for too long."""
        # A long gap may hide another car. A running charge goes on but no
        # longer counts as complete; a baseline without a car, and a charge
        # waiting for its final reading, do not survive it.
        if self._active and self._cycle_start is not None:
            self._cycle_partial = True
        else:
            self._cycle_start, self._cycle_partial = None, False
        self._unplugged = False
        self._closing_start = None

    async def _save(self) -> None:
        try:
            await self._store.async_save(self.last)
        except (ValueError, OSError):
            self._storage_notice.failed("estimate not saved; retrying without affecting charging")
            self._retry_at = time.monotonic() + 60
        else:
            self._dirty = False
            self._storage_notice.recovered("estimate saved again")

    async def async_stop(self) -> None:
        self._closed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_report is not None:
            self._unsubscribe_report()
            self._unsubscribe_report = None
        if self._expiry is not None:
            self._expiry()
            self._expiry = None
        if self._dirty:
            await self._save()
