"""Optional BavarianData adapters; all manufacturer details stay in this package."""

from collections.abc import Callable, Collection
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.types import HistoryMatch

from .departure import read_bavarian_departure
from .history import async_read_history
from .identity import BavarianVehicleBinding


class BavarianDepartureSource(BavarianVehicleBinding):
    """Translate one selected vehicle's published climate timer into an instant."""

    def __init__(
        self, hass: HomeAssistant, device_id: str, *, time_zone: str,
        timer1_days: Collection[str], timer2_days: Collection[str],
        expected_source_key: str | None = None,
    ) -> None:
        super().__init__(hass, device_id, expected_source_key=expected_source_key)
        self._time_zone = time_zone
        self._timer1_days = timer1_days
        self._timer2_days = timer2_days

    def next_departure(self, now: datetime) -> datetime | None:
        if not self.available:
            return None
        return read_bavarian_departure(
            self._hass, self._device_id, now=now, time_zone=self._time_zone,
            timer1_days=self._timer1_days, timer2_days=self._timer2_days,
        )


class BavarianChargeHistorySource(BavarianVehicleBinding):
    """Match public local history without exposing VINs to charging observers."""

    async def async_match(self, session: CompletedSession) -> HistoryMatch | None:
        if not self.available:
            return None
        return await async_read_history(
            self._hass, self._device_id, session, expected_vin=self._vin,
        )

    def subscribe(self, on_change: Callable[[], None]) -> CALLBACK_TYPE:
        @callback
        def changed(_: Event) -> None:
            # Another car's event may prompt a local read; matching is still
            # bound to the selected vehicle before and after the service call.
            on_change()

        @callback
        def registry_changed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
            if event.data.get("device_id") == self._device_id:
                on_change()

        unsubs = (
            self._hass.bus.async_listen("bavariandata_charging_stopped", changed),
            self._hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, registry_changed),
        )

        @callback
        def unsubscribe() -> None:
            for unsub in unsubs:
                unsub()

        return unsubscribe
