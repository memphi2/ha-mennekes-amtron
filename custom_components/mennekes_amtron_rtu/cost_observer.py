"""Optional local price observations and bounded charging-cost estimates."""

from __future__ import annotations

import logging
import math
import time
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
from .device.charging_cost import CostAccumulator, CostResult, TariffConfig
from .device.charging_session import CompletedSession
from .device.const import CONF_MEASUREMENT_INTERVAL_SECONDS, CONF_SCAN_INTERVAL_SECONDS
from .device.data import WallboxData
from .device.registers import CP_STATE, ENERGY_TOTAL, EVSE_STATE
from .device.vehicle import vehicle_connected
from .entry_types import MennekesAmtronConfigEntry
from .goal_store import ModelStore

_LOGGER = logging.getLogger(__name__)
PRICE_VALID_SECONDS = 90 * 60
_PRICE_UNITS = {"EUR/kWh": 1.0, "€/kWh": 1.0, "ct/kWh": 0.01, "EUR/MWh": 0.001, "€/MWh": 0.001}


def electricity_price(state: State | None) -> float | None:
    """Normalize explicit euro prices, including legitimate negative prices."""
    if state is None or state.attributes.get("restored") is True:
        return None
    factor = _PRICE_UNITS.get(str(state.attributes.get("unit_of_measurement")))
    if factor is None:
        return None
    try:
        price = float(state.state) * factor
    except ValueError:
        return None
    return price if math.isfinite(price) else None


class LastChargingCost:
    """One completed result; tariff changes never reprice an old charge."""

    def __init__(self) -> None:
        self.session: CompletedSession | None = None
        self.result: CostResult | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "session": self.session.as_dict() if self.session else None,
            "result": self.result.as_dict() if self.result else None,
        }

    @classmethod
    def restore(cls, raw: object) -> LastChargingCost:
        if not isinstance(raw, dict) or raw.keys() != {"session", "result"}:
            raise ValueError("invalid charging cost storage")
        result = cls()
        if raw["session"] is not None:
            result.session = CompletedSession.parse(raw["session"])
            result.result = CostResult.parse(raw["result"])
            if not math.isclose(result.result.energy_kwh, result.session.wallbox_kwh, rel_tol=1e-9):
                raise ValueError("charging cost energy does not match its session")
        elif raw["result"] is not None:
            raise ValueError("charging cost without a session")
        return result


class ChargingCostObserver:
    """Price only intervals observed by this HA process, in both connection modes."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry, *, remote: bool) -> None:
        self._hass, self._entry, self._remote = hass, entry, remote
        self._entity = str(entry.options.get(CONF_CHARGING_PRICE_ENTITY, ""))
        self._fixed = entry.options.get(CONF_CHARGING_COST_SOURCE) == COST_SOURCE_FIXED
        self._tariff = TariffConfig.parse(entry.options.get(CONF_CHARGING_NETWORK_TARIFF, {"module": "none"}))
        max_gap = 660 if remote else max(600, float(entry.options.get(CONF_MEASUREMENT_INTERVAL_SECONDS, 30)) + float(entry.options.get(CONF_SCAN_INTERVAL_SECONDS, 5)) + 60)
        self._accumulator = CostAccumulator(self._tariff, max_gap=max_gap)
        self._store = ModelStore(
            hass, entry.entry_id, entry.runtime_data.identity.serial_number or entry.entry_id,
            kind="charging_cost", model=LastChargingCost,
        )
        self.last = LastChargingCost()
        self._supported = False
        self._anchored = False
        self._active = False
        self._started_at = time.time()
        self._price_at = self._started_at
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._unsubscribe_report: CALLBACK_TYPE | None = None
        self._expiry: CALLBACK_TYPE | None = None
        self._live_price: float | None = None
        self._closed = False
        self._storage_failed = False
        self._storage_corrupt = False
        self._dirty = False
        self._retry_at = 0.0

    @property
    def current(self) -> CostResult | None:
        if not self._active:
            return None
        result = self._accumulator.result
        if self._anchored:
            return result
        return CostResult(result.known_cost, result.unpriced_kwh, result.energy_kwh, False)

    @property
    def available(self) -> bool:
        return not self._storage_corrupt and self._supported

    def _price(self) -> float | None:
        if self._fixed:
            return float(self._entry.options[CONF_CHARGING_PRICE])
        return self._live_price

    async def async_start(self) -> None:
        try:
            self.last = await self._store.async_load()
        except (ValueError, OSError):
            self._storage_corrupt = True
            _LOGGER.warning("Charging-cost storage cannot be read; optional cost estimates are unavailable")
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
        price = electricity_price(state)
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
            self._accumulator.observe_energy(now, None)
            self._anchored = False
            return
        self._supported = data.session_summary_supported and (not self._remote or data.energy_observed_at is not None)
        present = vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=self._entry.runtime_data.identity.layout_version >= CP_STATE.min_layout,
        )
        if present is None:
            self._anchored = False
        elif present:
            self._active = True
        at = (data.energy_observed_at or 0.0) if self._remote else now
        fresh = data.energy_read and self._started_at <= at <= now + 5 and at >= self._price_at
        energy = data.get(ENERGY_TOTAL.key)
        energy = float(energy) if isinstance(energy, (float, int)) and not isinstance(energy, bool) else None
        if fresh:
            self._accumulator.observe_energy(at, energy)
        latest = CompletedSession.parse(data.charging_session) if data.charging_session else None
        previous = self.last.session
        if data.session_summary_supported and latest != previous:
            candidate = LastChargingCost()
            candidate.session = latest
            if latest is not None:
                result = self._accumulator.result
                if self._anchored and fresh and math.isclose(result.energy_kwh, latest.wallbox_kwh, rel_tol=1e-9):
                    candidate.result = result
                else:
                    candidate.result = CostResult(0.0, latest.wallbox_kwh, latest.wallbox_kwh, False)
            self.last = candidate
            self._dirty = True
        if present is False:
            self._active = False
            if fresh and energy is not None and math.isfinite(energy) and energy >= 0:
                self._accumulator.reset()
                self._accumulator.set_price(at, self._price())
                self._accumulator.observe_energy(at, energy)
                self._anchored = True
        if self._dirty and time.monotonic() >= self._retry_at:
            await self._save()

    async def _save(self) -> None:
        try:
            await self._store.async_save(self.last)
        except (ValueError, OSError):
            if not self._storage_failed:
                _LOGGER.warning("Charging-cost estimate could not be saved; retrying without affecting charging")
            self._storage_failed = True
            self._retry_at = time.monotonic() + 60
        else:
            self._dirty = self._storage_failed = False

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
