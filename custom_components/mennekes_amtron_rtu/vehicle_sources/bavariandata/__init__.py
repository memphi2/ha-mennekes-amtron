"""Optional BavarianData adapters; all manufacturer details stay in this package."""

import hashlib
from collections.abc import Callable, Collection
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.types import HistoryMatch

from .departure import read_bavarian_departure
from .history import async_read_history, bavarian_vehicle_vin


class BavarianDepartureSource:
    """Translate one selected vehicle's published climate timer into an instant."""

    def __init__(
        self, hass: HomeAssistant, device_id: str, *, time_zone: str,
        timer1_days: Collection[str], timer2_days: Collection[str],
    ) -> None:
        self._hass = hass
        self._device_id = device_id
        self._vin = bavarian_vehicle_vin(hass, device_id)
        self._time_zone = time_zone
        self._timer1_days = timer1_days
        self._timer2_days = timer2_days

    @property
    def available(self) -> bool:
        return self._vin is not None and bavarian_vehicle_vin(self._hass, self._device_id) == self._vin

    def next_departure(self, now: datetime) -> datetime | None:
        if not self.available:
            return None
        return read_bavarian_departure(
            self._hass, self._device_id, now=now, time_zone=self._time_zone,
            timer1_days=self._timer1_days, timer2_days=self._timer2_days,
        )


class BavarianChargeHistorySource:
    """Match public local history without exposing VINs to charging observers."""

    def __init__(self, hass: HomeAssistant, device_id: str) -> None:
        self._hass = hass
        self._device_id = device_id
        self._vin = bavarian_vehicle_vin(hass, device_id)

    @property
    def source_key(self) -> str:
        # Preserve existing durable aggregates across the generic option rename.
        identity = f"bavariandata:{self._device_id}"
        if self._vin is not None:
            identity += ":" + hashlib.sha256(self._vin.encode()).hexdigest()
        return identity

    @property
    def available(self) -> bool:
        return self._vin is not None and bavarian_vehicle_vin(self._hass, self._device_id) == self._vin

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

        return self._hass.bus.async_listen("bavariandata_charging_stopped", changed)
