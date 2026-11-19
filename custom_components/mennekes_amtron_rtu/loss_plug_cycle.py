"""The loss of a plug cycle, measured against an energy value the car reports."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import async_track_state_change_event

from .charge_loss_const import (
    CONF_LOSS_AUTO_MATCH,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    LOSS_MATCH_WINDOW_SECONDS,
    LOSS_SOURCE_SENSOR,
)
from .charge_watch import PlugCycle, last_cycle
from .device.charge_loss import Charge, LossStatistics
from .device.client_errors import AmtronConnectionError
from .device.errors import DeviceError
from .device.meter_log import MAX_INTERPOLATION_SECONDS
from .device.vehicle import snapshot_vehicle_connected
from .exceptions import service_validation_error
from .loss_ledger import LossLedger
from .vehicle_profiles import CONF_VEHICLE_PROFILE_ID

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry


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


@dataclass(frozen=True, slots=True)
class Booking:
    """What a decided plug cycle still has to book: its charge, or none if unmeasurable."""

    charge: Charge | None


class PlugCycleLoss:
    """Decides each plug cycle of the service once and matches the car's value to it.

    Three things are kept apart. ``cycle`` is the last plug cycle that was
    decided. ``charge`` is what a value of the car may go to, and only while
    that cycle is still the service's last. ``unbooked`` is what the decision
    still owes the saved statistics: it stays until a save succeeded, so a
    later cycle of another car cannot lose it.
    """

    available = True

    def __init__(
        self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry, ledger: LossLedger,
        options: Mapping[str, Any], queue: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._ledger = ledger
        self._queue = queue
        self._entity = str(options.get(CONF_VEHICLE_LAST_CHARGE_ENERGY, ""))
        self._auto = options.get(CONF_LOSS_AUTO_MATCH, False)
        self._profile_id: str | None = options.get(CONF_VEHICLE_PROFILE_ID)
        self.cycle: PlugCycle | None = None
        self.charge: Charge | None = None
        self.unbooked: Booking | None = None
        # A changed value of the car, until it is matched or superseded.
        self._change: tuple[float, float] | None = None
        self._started_at = time.time()

    def ready(self) -> bool:
        self._ledger.statistics.configure_source(f"{LOSS_SOURCE_SENSOR}:{self._entity}")
        return True

    def subscribe(self) -> list[CALLBACK_TYPE]:
        if not self._entity:
            return []
        return [async_track_state_change_event(self._hass, [self._entity], self._vehicle_changed)]

    def stop(self) -> None:
        """Nothing of its own to cancel."""

    @callback
    def _vehicle_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = vehicle_energy(event.data["old_state"]), vehicle_energy(event.data["new_state"])
        if self._auto and old is not None and new is not None and new != old:
            self._change = (event.time_fired.timestamp(), new)
            self._queue()

    def wants(self, *, initial: bool) -> bool:
        """A plug cycle of the service is still to be decided."""
        del initial
        cycle = last_cycle(self._entry)
        return cycle is not None and cycle != self.cycle

    @property
    def assignable(self) -> Charge | None:
        """The charge a value of the car may go to; none while a newer cycle is open."""
        decided = self.cycle is not None and last_cycle(self._entry) == self.cycle
        return self.charge if decided else None

    def _unplugged(self) -> bool:
        coordinator = self._entry.runtime_data.coordinator
        return coordinator.last_update_success and snapshot_vehicle_connected(
            coordinator.current_data().get, self._entry.runtime_data.identity.layout_version,
        ) is False

    @property
    def can_assign(self) -> bool:
        return self.assignable is not None and self._unplugged()

    async def _decide(self) -> None:
        """Decide the service's last plug cycle once, as soon as that is possible."""
        cycle = last_cycle(self._entry)
        if cycle is None or cycle == self.cycle:
            return
        if self._profile_id is not None and (cycle.owner is None or cycle.owner.id != self._profile_id):
            # Another car's charge: this profile keeps its last match and average.
            self.cycle, self.charge = cycle, None
            return
        if (known := self._ledger.statistics.known(cycle.plugged_at, cycle.unplugged_at)) is not None:
            # Seen again after a restart: it is accounted for.
            self.cycle, self.charge = cycle, known
            return
        try:
            energy = await self._entry.runtime_data.client.async_energy_between(
                cycle.plugged_at, cycle.unplugged_at,
            )
        except (AmtronConnectionError, DeviceError):
            # No answer is no measurement: asked again at the next poll.
            return
        if energy is None and time.time() - cycle.unplugged_at <= MAX_INTERPOLATION_SECONDS:
            # The reading after the unplug is still to come.
            return
        self.cycle = cycle
        if energy == 0:
            # Plugged in without charging: everything stays as it was. After
            # a restart nothing is assignable: a charge unseen meanwhile may
            # lie between the saved one and this plug-in.
            return
        # A cycle the meter cannot measure is no longer the charge before it.
        self.charge = Charge(cycle.plugged_at, cycle.unplugged_at, energy) if energy is not None else None
        self.unbooked = Booking(self.charge)

    def _matching(self, charge: Charge | None) -> tuple[float, float] | None:
        """The car's changed value, if it followed the end of this charge closely."""
        change = self._change
        if charge is None or not self._auto or change is None or not self._unplugged():
            return None
        return change if self._started_at <= charge.end <= change[0] <= charge.end + LOSS_MATCH_WINDOW_SECONDS else None

    async def step(self) -> None:
        await self._decide()
        charge = self.assignable
        unbooked, change = self.unbooked, self._matching(charge)
        if (unbooked is None and change is None) or self._ledger.resting:
            # Nothing to book, or a save just failed: the poll after the rest comes back.
            return
        async with self._ledger.lock:
            if self._ledger.closed:
                return

            def book(statistics: LossStatistics) -> None:
                if unbooked is not None:
                    statistics.observe_charge(unbooked.charge)
                # An assignment by hand meanwhile supersedes the changed value.
                if charge is not None and change is not None and self._change is change:
                    statistics.apply(charge, change[1], association="changed_value")

            if await self._ledger.commit(book):
                if self.unbooked is unbooked:
                    self.unbooked = None
                if self._change is change:
                    self._change = None

    async def assign(self, allowed: Callable[[], bool]) -> None:
        """Associate the car's current value with the last charge."""
        async with self._ledger.lock:
            charge = self.assignable
            if self._ledger.closed or not allowed() or charge is None:
                raise service_validation_error("loss_unavailable")
            energy = vehicle_energy(self._hass.states.get(self._entity))
            if energy is None or energy > charge.wallbox_kwh:
                raise service_validation_error("loss_energy_invalid")
            # The explicit assignment supersedes older buffered estimates.
            # Events arriving during the save form a new candidate and survive.
            self._change = None
            if not await self._ledger.commit(
                lambda statistics: statistics.apply(charge, energy, association="manual"),
            ):
                raise service_validation_error("loss_storage_unavailable")
