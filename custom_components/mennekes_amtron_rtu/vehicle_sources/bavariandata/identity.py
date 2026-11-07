"""Resolve and retain one local BavarianData vehicle across optional sources."""

from __future__ import annotations

import hashlib
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

DOMAIN = "bavariandata"


def bavarian_vehicle_vin(hass: HomeAssistant, device_id: object) -> str | None:
    """Resolve a real vehicle, never BavarianData's account-wide debug device.

    BavarianData identifies vehicle devices as (domain, VIN), while its debug
    device uses the config-entry ID. A source must establish a single vehicle
    before reading its published entities or querying its local history.
    """
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 256:
        return None
    try:
        device = dr.async_get(hass).async_get(device_id)
    except RuntimeError:  # The registry is not ready yet.
        return None
    if device is None:
        return None
    candidates = {
        value.strip().upper() for domain, value in device.identifiers
        if domain == DOMAIN and value != device.config_entry_id
        and re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", value.strip().upper())
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def vehicle_source_key(device_id: str, vin: str | None) -> str:
    """Expose stable source identity without publishing the vehicle VIN."""
    identity = f"{DOMAIN}:{device_id}"
    return identity if vin is None else identity + ":" + hashlib.sha256(vin.encode()).hexdigest()


class BavarianVehicleBinding:
    """Bind once, including late registry setup, without following another VIN."""

    def __init__(self, hass: HomeAssistant, device_id: str, *, expected_source_key: str | None = None) -> None:
        self._hass = hass
        self._device_id = device_id
        self._expected_source_key = expected_source_key
        self._vin = self._matching_vin()

    def _matching_vin(self) -> str | None:
        vin = bavarian_vehicle_vin(self._hass, self._device_id)
        if self._expected_source_key is not None and vehicle_source_key(self._device_id, vin) != self._expected_source_key:
            return None
        return vin

    @property
    def source_key(self) -> str:
        # A persisted pin survives an unavailable registry. Keep the existing
        # key format so vehicle goals and loss aggregates retain their identity.
        return self._expected_source_key or vehicle_source_key(self._device_id, self._vin)

    @property
    def available(self) -> bool:
        current = self._matching_vin()
        if self._vin is None:
            self._vin = current
        return self._vin is not None and current == self._vin
