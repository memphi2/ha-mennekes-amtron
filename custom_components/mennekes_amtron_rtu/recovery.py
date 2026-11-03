"""Bounded recovery of a confirmed missing-heartbeat fault in master mode."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import ERROR_CODE_EMS_UNAVAILABLE, MIN_CURRENT_WRITE_INTERVAL_SECONDS
from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_RETRY_SECONDS = 30.0


class HeartbeatRecovery:
    """React to state changes without adding a polling or Recorder stream."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._task: asyncio.Task[None] | None = None
        self._retry: asyncio.TimerHandle | None = None
        self._remove: list[Callable[[], None]] = []
        self._attempts = 0
        self._stopped = False

    def start(self) -> None:
        runtime = self._entry.runtime_data
        self._remove.append(runtime.coordinator.async_add_listener(self.async_check))
        if runtime.heartbeat is not None:
            self._remove.append(runtime.heartbeat.async_add_listener(self.async_check))
        self.async_check()

    def _eligible(self) -> bool:
        runtime = self._entry.runtime_data
        return bool(
            not self._stopped
            and not runtime.control.read_only
            and runtime.heartbeat is not None
            and runtime.heartbeat.healthy is True
            and runtime.coordinator.last_update_success
            and runtime.coordinator.current_data().get("error_code")
            == ERROR_CODE_EMS_UNAVAILABLE
            and runtime.control.is_paused is False
            and runtime.coordinator.current_data().get("lock_evse") == 0
            and self._attempts < MAX_RECOVERY_ATTEMPTS
        )

    def async_check(self) -> None:
        runtime = self._entry.runtime_data
        if (
            runtime.coordinator.last_update_success
            and runtime.coordinator.current_data().get("error_code") == 0
        ):
            self._attempts = 0
        if not self._eligible():
            self._cancel_retry()
            return
        if self._task is None and self._retry is None:
            self._task = self._entry.async_create_background_task(
                self._hass,
                self._async_recover(),
                "mennekes_amtron_rtu_recovery",
                # Publish ownership before callbacks or an immediate cooldown
                # can finish the coroutine and clear it again.
                eager_start=False,
            )

    def _cancel_retry(self) -> None:
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None

    def _retry_due(self) -> None:
        self._retry = None
        self.async_check()

    async def _async_recover(self) -> None:
        retry_after: float | None = None
        runtime = self._entry.runtime_data
        try:
            attempted = await runtime.control.async_recover_from_error(automatic=True)
            if attempted:
                self._attempts += 1
                retry_after = RECOVERY_RETRY_SECONDS
            # This must complete a poll, not merely queue a debounced refresh.
            await runtime.coordinator.async_refresh()
            if (
                runtime.coordinator.last_update_success
                and runtime.coordinator.current_data().get("error_code") == 0
            ):
                self._attempts = 0
                retry_after = None
        except ServiceValidationError as err:
            if err.translation_key == "write_rate_limited":
                retry_after = MIN_CURRENT_WRITE_INTERVAL_SECONDS
            else:
                self._attempts += 1
                retry_after = RECOVERY_RETRY_SECONDS
                _LOGGER.debug("Automatic heartbeat recovery postponed: %s", err)
        except HomeAssistantError as err:
            self._attempts += 1
            retry_after = RECOVERY_RETRY_SECONDS
            _LOGGER.debug("Automatic heartbeat recovery failed: %s", err)
        finally:
            self._task = None
        if retry_after is not None and self._eligible():
            self._retry = self._hass.loop.call_later(retry_after, self._retry_due)

    async def async_stop(self) -> None:
        self._stopped = True
        self._cancel_retry()
        for remove in self._remove:
            remove()
        self._remove.clear()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None
