"""Report sustained heartbeat/link failures without depending on Modbus reads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed

from .entry_types import MennekesAmtronConfigEntry
from .remote import RemoteClient
from .repair_issues import (
    ISSUE_HEARTBEAT_UNAVAILABLE,
    ISSUE_SERVICE_UNAVAILABLE,
    heartbeat_lost,
)

HEARTBEAT_REPAIR_DELAY = 60.0
_ISSUES = (ISSUE_HEARTBEAT_UNAVAILABLE, ISSUE_SERVICE_UNAVAILABLE)


class HeartbeatRepairMonitor:
    """Own one grace timer and publish only confirmed health transitions.

    Heartbeat delivery can fail while no error register can be read. Conversely,
    losing HA's HTTP connection does not prove that the independent app stopped
    its heartbeat. Keep these cases separate from the wallbox's error 200.
    """

    def __init__(
        self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry,
        publish: Callable[[str, bool], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._publish_issue = publish
        self._timer: asyncio.TimerHandle | None = None
        self._active: str | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._closed = False

    @callback
    def start(self) -> None:
        runtime = self._entry.runtime_data
        self._unsubs.append(runtime.coordinator.async_add_listener(self.check))
        if runtime.heartbeat is not None:
            self._unsubs.append(runtime.heartbeat.async_add_listener(self.check))
        self._entry.async_on_unload(self.stop)
        self.check()

    def _fault(self) -> str | None:
        if self._hass.is_stopping or self._entry.state not in (
            ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS,
        ):
            return None
        runtime = self._entry.runtime_data
        if isinstance(runtime.coordinator.last_exception, ConfigEntryAuthFailed):
            # HA already asks for the corrected credentials. The intentionally
            # stopped heartbeat must not add an unrelated connectivity repair.
            return None
        heartbeat = runtime.heartbeat
        if heartbeat is None:
            return None
        if isinstance(runtime.client, RemoteClient):
            # RemoteControl.read_only also means temporarily unreachable. Use
            # the last authenticated app policy instead, including HTTP outages.
            if runtime.client.snapshot.get("control", {}).get("read_only") is not False:
                return None
            if not runtime.client.connected:
                return ISSUE_SERVICE_UNAVAILABLE
        elif runtime.control.read_only:
            return None
        if heartbeat.healthy is not False:
            return None
        coordinator = runtime.coordinator
        if coordinator.last_update_success and heartbeat_lost(coordinator.current_data()):
            # The existing error-200 repair already identifies a confirmed
            # firmware fault. Do not duplicate it with a generic heartbeat issue.
            return None
        return ISSUE_HEARTBEAT_UNAVAILABLE

    @callback
    def check(self) -> None:
        if self._closed:
            return
        fault = self._fault()
        if fault is None:
            self._cancel_timer()
            self._set_active(None)
        elif self._active is not None:
            # A sustained outage can change from an unreachable app to a
            # reachable app reporting a dead bus. Update the explanation now.
            self._set_active(fault)
        elif self._timer is None:
            self._timer = self._hass.loop.call_later(HEARTBEAT_REPAIR_DELAY, self._expired)

    @callback
    def _expired(self) -> None:
        self._timer = None
        if not self._closed:
            # Re-evaluate current evidence; a queued timer must never publish
            # an old failure after recovery, permission changes or unloading.
            self._set_active(self._fault())

    def _set_active(self, issue: str | None) -> None:
        self._active = issue
        for candidate in _ISSUES:
            self._publish_issue(candidate, candidate == issue)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @callback
    def stop(self) -> None:
        self._closed = True
        self._cancel_timer()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._set_active(None)
