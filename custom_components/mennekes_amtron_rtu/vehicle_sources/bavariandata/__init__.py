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
from .history import async_read_history, bound_vehicle_vin, vehicle_source_key


class BavarianDepartureSource:
    """Translate one selected vehicle's published climate timer into an instant."""

    def __init__(
        self, hass: HomeAssistant, device_id: str, *, time_zone: str,
        timer1_days: Collection[str], timer2_days: Collection[str],
        expected_source_key: str | None = None,
    ) -> None:
        self._hass = hass
        self._device_id = device_id
        self._expected_source_key = expected_source_key
        self._vin = bound_vehicle_vin(hass, device_id, expected_source_key)
        self._time_zone = time_zone
        self._timer1_days = timer1_days
        self._timer2_days = timer2_days

    @property
    def available(self) -> bool:
        current = bound_vehicle_vin(self._hass, self._device_id, self._expected_source_key)
        if self._vin is None:
            self._vin = current
        return self._vin is not None and current == self._vin

    def next_departure(self, now: datetime) -> datetime | None:
        if not self.available:
            return None
        return read_bavarian_departure(
            self._hass, self._device_id, now=now, time_zone=self._time_zone,
            timer1_days=self._timer1_days, timer2_days=self._timer2_days,
        )


class BavarianChargeHistorySource:
    """Match public local history without exposing VINs to charging observers."""

    def __init__(self, hass: HomeAssistant, device_id: str, *, expected_source_key: str | None = None) -> None:
        self._hass = hass
        self._device_id = device_id
        self._expected_source_key = expected_source_key
        self._vin = bound_vehicle_vin(hass, device_id, expected_source_key)

    @property
    def source_key(self) -> str:
        # Preserve existing durable aggregates across the generic option rename.
        return self._expected_source_key or vehicle_source_key(self._device_id, self._vin)

    @property
    def available(self) -> bool:
        current_vin = bound_vehicle_vin(self._hass, self._device_id, self._expected_source_key)
        if self._vin is None:
            # A vehicle registry entry can appear after this observer starts.
            # Bind once; a later replacement must never be followed implicitly.
            self._vin = current_vin
        return self._vin is not None and current_vin == self._vin

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
