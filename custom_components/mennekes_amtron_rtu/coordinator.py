"""Fetch the service's state for Home Assistant's entities."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .data import DeviceIdentity, WallboxData
from .device.client_errors import AmtronConnectionError
from .device.const import DOMAIN
from .service_errors import ServiceConnectionError

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry
    from .service_client import ServiceClient

_LOGGER = logging.getLogger(__name__)

# The service stamps the due time without fractions of a second.
_PENDING_GRACE_SECONDS = 1.0


class ServiceCoordinator(DataUpdateCoordinator[WallboxData]):
    """Poll the service's cache; the service schedules its own bus reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MennekesAmtronConfigEntry,
        *,
        client: ServiceClient,
        scan_interval: float,
        credentials_rejected: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="MENNEKES AMTRON",
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )
        self._client = client
        self._credentials_rejected = credentials_rejected
        # Estimates follow every poll, a failed one as None.
        self.observe_estimates: Callable[[WallboxData | None], Awaitable[None]] | None = None
        self._pending_refresh: CALLBACK_TYPE | None = None
        # A pushed poll, the timer and a command's own refresh may meet: the
        # estimates see one sample at a time.
        self._update_lock = asyncio.Lock()
        client.on_auth_error = self._publish_auth_error

    @property
    def identity(self) -> DeviceIdentity:
        return self._client.identity

    def current_data(self) -> WallboxData:
        """Return the newest state, or an empty one before the first poll."""
        return self.data or WallboxData()

    def _follow_pending(self, until: datetime | None) -> None:
        """Read a queued command's outcome when it is due, not a poll later.

        The service runs the command on its own; without this, HA learned of
        it up to one poll interval later, together with whatever came next.
        """
        if self._pending_refresh is not None:
            self._pending_refresh()
            self._pending_refresh = None
        if until is None or self.update_interval is None:
            return
        delay = (until - datetime.now(UTC)).total_seconds() + _PENDING_GRACE_SECONDS
        # A later due time is followed by an ordinary poll before it.
        if 0 < delay < self.update_interval.total_seconds():
            self._pending_refresh = async_call_later(self.hass, delay, self._pending_due)

    @callback
    def _pending_due(self, _now: datetime) -> None:
        self._pending_refresh = None
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        self._follow_pending(None)
        await super().async_shutdown()

    def _publish_auth_error(self, new_failure: bool) -> None:
        """Expose a changed failure reason even when already unavailable."""
        was_available = self.last_update_success
        self.async_set_update_error(ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="service_auth_failed"
        ))
        if new_failure and not was_available:
            # HA suppresses notifications for consecutive failed refreshes.
            # Repair listeners must still see this distinct authentication error,
            # including one reported by a command or diagnostics request.
            self.async_update_listeners()

    async def _async_refresh(self, *args: Any, **kwargs: Any) -> None:
        # One refresh at a time, from its start to its notification: two
        # that overlap would each compare with the state before both, and a
        # recovery right after a failure would notify nobody.
        async with self._update_lock:
            await super()._async_refresh(*args, **kwargs)

    async def _async_update_data(self) -> WallboxData:
        return await self._fetch()

    async def _fetch(self) -> WallboxData:
        try:
            data = await self._client.update()
        except AmtronConnectionError as err:
            if self.observe_estimates is not None:
                await self.observe_estimates(None)
            if self._credentials_rejected is not None and self._credentials_rejected():
                auth_error = ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="gateway_authentication_failed"
                )
                if not self.last_update_success and not isinstance(
                    self.last_exception, ConfigEntryAuthFailed
                ):
                    # HA notifies nobody of a second failed poll in a row;
                    # an earlier connection repair must still see this cause,
                    # once.
                    self.async_set_update_error(auth_error)
                    self.async_update_listeners()
                raise auth_error from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_auth_failed":
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="service_auth_failed"
                ) from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_identity_changed":
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="device_identity_changed"
                ) from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_version_mismatch":
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="service_version_mismatch"
                ) from err
            raise UpdateFailed(str(err)) from err
        if self.observe_estimates is not None:
            await self.observe_estimates(data)
        self._follow_pending(data.pending_change_until)
        return data
