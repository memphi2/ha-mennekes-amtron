"""Optional vehicle-based grants; the bus owner enforces the physical gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .device.client_errors import AmtronBusError
from .device.errors import DeviceError
from .entry_types import MennekesAmtronConfigEntry
from .vehicle_detection import SETTLE_SECONDS, VehicleEvidence
from .vehicle_profiles import (
    CONF_VEHICLE_PLUG_CHARGE,
    CONF_VIRTUAL_PLUG_CHARGE,
    active_profile,
    profiles,
)

_LOGGER = logging.getLogger(__name__)


def plug_charge_policy(options: Mapping[str, Any]) -> str:
    """Revoke grants when permissions/identity/evidence change, not on reload."""
    policy = {
        key: {
            "provider": profile.provider_id,
            "device": profile.device_id,
            "identity": profile.identity,
            "detection": profile.detection.as_dict() if profile.detection else None,
            "permitted": profile.options.get(CONF_VEHICLE_PLUG_CHARGE) is True,
        }
        for key, profile in profiles(options).items()
    }
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


class PlugChargeObserver:
    """Grant a bounded physical session after fresh, unique vehicle evidence."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._control = entry.runtime_data.control
        self._coordinator = entry.runtime_data.coordinator
        self._evidence = VehicleEvidence(hass, entry)
        self._policy = plug_charge_policy(entry.options)
        self._session: str | None = None
        self._candidate: tuple[str, str] | None = None
        self._timer: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[None] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._retry_at = 0.0
        self._generation = 0
        self._status = "waiting_vehicle"

    @property
    def status(self) -> str:
        """A stable enum, without session tokens or ticking age attributes."""
        return self._status

    @callback
    def start(self) -> None:
        self._unsubs = [
            self._evidence.subscribe(self._changed),
            self._coordinator.async_add_listener(self._changed),
        ]
        self._changed()

    def _selection(self) -> tuple[str, str] | None:
        gate = self._control.plug_charge
        if (
            self._stopped or not self._evidence.current()
            or self._entry.options.get(CONF_VIRTUAL_PLUG_CHARGE) is not True
            or gate.get("policy") != self._policy
        ):
            self._set_status("disabled")
            return None
        if not self._coordinator.last_update_success or not gate.get("enabled"):
            self._set_status("blocked")
            return None
        session = gate.get("session")
        session = session if isinstance(session, str) else None
        if session != self._session:
            self._session = session
            self._retry_at = 0.0
        if not isinstance(session, str):
            self._set_status("waiting_vehicle")
            return None
        # Once accepted, stale cloud data cannot interrupt this physical cycle.
        # A policy change or lost physical connection is revoked by the owner.
        if gate.get("authorized"):
            self._set_status("authorized")
            return None
        selected = self._evidence.selected()
        if selected is None:
            self._set_status("waiting_detection")
            return None
        profile = active_profile(self._entry.options)
        if (
            profile is None or profile.id != selected
            or profile.options.get(CONF_VEHICLE_PLUG_CHARGE) is not True
        ):
            self._set_status("vehicle_not_permitted")
            return None
        evidence = self._evidence.snapshot(selected)
        raw_since = gate.get("since")
        since = dt_util.parse_datetime(raw_since) if isinstance(raw_since, str) else None
        if (
            evidence.connected_at is None or evidence.connected_at.tzinfo is None
            or since is None or since.tzinfo is None or evidence.connected_at < since
        ):
            self._set_status("waiting_detection")
            return None
        values = self._coordinator.current_data().values
        if (
            self._control.read_only or self._control.pause_requested is True
            or self._control.charging_release_requested is False
            or self._control.energy_goal_stop_requested
            or values.get("lock_evse") != 0 or values.get("error_code") != 0
            or values.get("authorization_enabled") != 0
        ):
            self._set_status("blocked")
            return None
        self._set_status("waiting_release")
        return selected, session

    def _set_status(self, status: str) -> None:
        if self._status != status:
            self._status = status
            # Defer listeners to avoid recursion through our own subscription.
            self._hass.loop.call_soon(self._notify)

    @callback
    def _notify(self) -> None:
        if not self._stopped:
            self._coordinator.async_update_listeners()

    @callback
    def _changed(self, _event: object = None) -> None:
        selected = self._selection()
        if selected != self._candidate:
            self._cancel_timer()
            self._candidate = selected
            gate = self._control.plug_charge
            if (
                self._task is not None and not self._task.done()
                and not (gate.get("authorized") and gate.get("session") == self._session)
            ):
                self._task.cancel()
        if selected is None or self._timer is not None or (
            self._task is not None and not self._task.done()
        ) or time.monotonic() < self._retry_at:
            return
        generation = self._generation

        @callback
        def settled(now: object) -> None:
            if generation == self._generation:
                self._settled(now)

        self._timer = async_call_later(self._hass, SETTLE_SECONDS, settled)

    @callback
    def _settled(self, _now: object) -> None:
        self._timer = None
        candidate = self._candidate
        if candidate is not None and self._selection() == candidate:
            self._task = self._hass.async_create_task(self._authorize(candidate))

    async def _authorize(self, candidate: tuple[str, str]) -> None:
        try:
            if self._selection() == candidate:
                await self._control.async_authorize_plug_charge(
                    candidate[1], deadline=dt_util.utcnow() + timedelta(seconds=10),
                )
                self._selection()
        except (AmtronBusError, HomeAssistantError, DeviceError, OSError) as err:
            # The owner may still be in its configured switching interval.
            # Retry on a later ordinary poll/report, never spin or force polls.
            _LOGGER.debug("Virtual charging release deferred: %s", err)
        finally:
            self._retry_at = time.monotonic() + 5

    def _cancel_timer(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer()
            self._timer = None

    async def async_stop(self) -> None:
        """Detach observers; a confirmed app grant survives an HA restart."""
        self._stopped = True
        self._cancel_timer()
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
