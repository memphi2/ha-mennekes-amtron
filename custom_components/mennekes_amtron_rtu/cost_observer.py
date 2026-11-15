"""Optional local price observations and bounded charging-cost estimates."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from itertools import islice
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
from .device.charging_session import CompletedSession, PresenceGap
from .device.const import CONF_MEASUREMENT_INTERVAL_SECONDS, CONF_SCAN_INTERVAL_SECONDS
from .device.data import WallboxData
from .device.enums import EvseState
from .device.notice import FailureNotice
from .device.registers import ENERGY_TOTAL, EVSE_STATE
from .device.vehicle import snapshot_metered_vehicle_present
from .entry_types import MennekesAmtronConfigEntry
from .goal_store import ModelStore

_LOGGER = logging.getLogger(__name__)
PRICE_VALID_SECONDS = 90 * 60
# Possible session baselines: one run per distinct meter value read while not
# charging, so even many long charges fit.
_MAX_METER_RUNS = 4096


@dataclass(frozen=True, slots=True)
class _MeterRun:
    """Consecutive non-charging readings with one meter value."""

    first: CostCheckpoint
    last: CostCheckpoint


_PRICE_UNITS = {"EUR/kWh": 1.0, "€/kWh": 1.0, "ct/kWh": 0.01, "EUR/MWh": 0.001, "€/MWh": 0.001}
_RETAINED_OPTIONS = (
    CONF_CHARGING_COST_SOURCE, CONF_CHARGING_PRICE, CONF_CHARGING_PRICE_ENTITY,
    CONF_CHARGING_NETWORK_TARIFF, CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
)


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
        self._configured_options = deepcopy(dict(entry.options))
        self._configured_data = dict(entry.data)
        self._identity = (
            entry.runtime_data.identity.serial_number, entry.runtime_data.identity.layout_version,
        )
        max_gap = 660 if remote else max(600, float(entry.options.get(CONF_MEASUREMENT_INTERVAL_SECONDS, 30)) + float(entry.options.get(CONF_SCAN_INTERVAL_SECONDS, 5)) + 60)
        self._accumulator = CostAccumulator(self._tariff, max_gap=max_gap)
        self._store = ModelStore(
            hass, entry.entry_id, entry.runtime_data.identity.serial_number or entry.entry_id,
            kind="charging_cost", model=LastChargingCost,
        )
        self.last = LastChargingCost()
        self._supported = False
        # The session decides which plug cycle is complete; costs only find
        # the observed meter interval with exactly its energy. No plug-cycle
        # state is duplicated here.
        self._runs: deque[_MeterRun] = deque(maxlen=_MAX_METER_RUNS)
        self._newest: CostCheckpoint | None = None
        self._active = False  # A car is plugged in: show the running cost.
        self._unplugged = False  # The last known poll saw no car.
        self._cycle_start: CostCheckpoint | None = None
        # The running cost started without an idle reading: never complete.
        self._cycle_partial = False
        self._gap = PresenceGap()
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

    def retain_for_reload(self, entry: MennekesAmtronConfigEntry, *, remote: bool) -> bool:
        """Keep observed prices and energy only across an unchanged live source.

        Continuity across a reconnect is judged by the presence-gap rule.
        Vehicle-profile changes do not change this wallbox-wide calculation.
        """
        return (
            not self._closed and not self._storage_corrupt
            and entry.entry_id == self._entry.entry_id
            and remote == self._remote
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
        if not self.retain_for_reload(entry, remote=self._remote):
            raise ValueError("charging-cost source changed during reload")
        self._entry = entry
        self._configured_options = deepcopy(dict(entry.options))

    def apply_tariff_only_update(self) -> bool:
        """Change only the cost model without restarting the Modbus connection."""
        current = deepcopy(dict(self._entry.options))
        # The shared entry listener also runs for credential and connection
        # changes. Only an actual tariff-options delta may bypass a reload.
        if current == self._configured_options or dict(self._entry.data) != self._configured_data:
            return False
        old_other = {key: value for key, value in self._configured_options.items() if key != CONF_CHARGING_NETWORK_TARIFF}
        new_other = {key: value for key, value in current.items() if key != CONF_CHARGING_NETWORK_TARIFF}
        if self._closed or old_other != new_other:
            return False
        try:
            tariff = TariffConfig.parse(current.get(CONF_CHARGING_NETWORK_TARIFF, {"module": "none"}))
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
    def current(self) -> CostResult | None:
        if not self._active:
            return None
        now = self._accumulator.checkpoint()
        if now is None or self._cycle_start is None:
            return CostResult(0.0, 0.0, 0.0, False)
        result = now.since(self._cycle_start)
        if self._cycle_partial and result.complete:
            return CostResult(result.known_cost, 0.0, result.energy_kwh, False)
        return result

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
            # One failed poll is no meter evidence: the next reading spans the
            # gap. A long gap may hide another car for the running estimate.
            self._presence(now, known=False)
            return
        self._supported = data.session_summary_supported and (not self._remote or data.energy_observed_at is not None)
        present = snapshot_metered_vehicle_present(data.get, self._entry.runtime_data.identity.layout_version)
        self._presence(now, known=present is not None)
        at = (data.energy_observed_at or 0.0) if self._remote else now
        fresh = data.energy_read and self._started_at <= at <= now + 5 and at >= self._price_at
        energy = data.get(ENERGY_TOTAL.key)
        energy = float(energy) if isinstance(energy, (float, int)) and not isinstance(energy, bool) else None
        charging = data.get(EVSE_STATE.key) == EvseState.CHARGING
        checkpoint = None
        # A failed or rejected read leaves the previous value, which must not
        # pass for a new reading.
        if fresh and energy is not None and self._accumulator.observe_energy(at, energy):
            checkpoint = self._accumulator.checkpoint()
            self._record(checkpoint, charging=charging)
        fresh = checkpoint is not None
        if present is True:
            replugged, self._unplugged, self._active = self._unplugged, False, True
            if self._cycle_start is None and fresh:
                # Right after an unplug and before charging, no energy has
                # flowed since; the session takes this reading as baseline too.
                self._cycle_start, self._cycle_partial = checkpoint, not (replugged and not charging)
            elif (checkpoint is not None and self._cycle_start is not None and not charging
                  and checkpoint.meter_kwh == self._cycle_start.meter_kwh):
                # Waiting plugged in: start where the session's baseline does.
                self._cycle_start = checkpoint
        elif present is False:
            # Only a reading while unplugged starts the next running cost; an
            # unplug without one must not carry this charge into the next.
            if fresh:
                self._cycle_start, self._cycle_partial = checkpoint, False
            elif self._active:
                self._cycle_start, self._cycle_partial = None, False
            self._active, self._unplugged = False, True
        latest = CompletedSession.parse(data.charging_session) if data.charging_session else None
        if data.session_summary_supported and latest != self.last.session:
            candidate = LastChargingCost()
            candidate.session = latest
            if latest is not None:
                candidate.result = self._session_cost(latest.wallbox_kwh)
            self.last = candidate
            self._dirty = True
        if self._dirty and time.monotonic() >= self._retry_at:
            await self._save()

    def _presence(self, now: float, *, known: bool) -> None:
        if self._gap.observe(now, known=known):
            self._cycle_start, self._cycle_partial, self._unplugged = None, False, False

    def _record(self, checkpoint: CostCheckpoint | None, *, charging: bool) -> None:
        if checkpoint is None:
            return
        self._newest = checkpoint
        tail = self._runs[-1] if self._runs else None
        # A charging reading at the idle value still marks the baseline, e.g.
        # an app sample taken just before charging started.
        if charging and (tail is None or tail.last.meter_kwh != checkpoint.meter_kwh):
            return
        if self._runs and self._runs[-1].last.meter_kwh == checkpoint.meter_kwh:
            self._runs[-1] = _MeterRun(self._runs[-1].first, checkpoint)
        else:
            self._runs.append(_MeterRun(checkpoint, checkpoint))

    def _session_cost(self, kwh: float) -> CostResult:
        """Price the interval from the last baseline reading to the newest one.

        Only the newest reading may end it: the session completes with its
        final meter value, and an older end could match another interval.
        """
        newest = self._newest
        if newest is None or not self._runs:
            return CostResult(0.0, kwh, kwh, False)
        # The earliest reading of the final value, before later idle gaps.
        tail = self._runs[-1]
        if tail.last.meter_kwh != newest.meter_kwh:
            # The newest reading was taken while charging; it is not final.
            return CostResult(0.0, kwh, kwh, False)
        end = tail.first
        target = end.meter_kwh - kwh
        for start in islice(reversed(self._runs), 1, None):
            if math.isclose(start.last.meter_kwh, target, rel_tol=1e-9, abs_tol=1e-6):
                return end.since(start.last, energy_kwh=kwh)
            if start.last.meter_kwh < target:
                break
        return CostResult(0.0, kwh, kwh, False)

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
