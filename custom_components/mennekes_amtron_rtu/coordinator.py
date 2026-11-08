"""Polling coordinator for one AMTRON wallbox.

The wallbox has no push channel, so the integration polls. Only the blocks
the device's register layout actually supports are read, and a block that
fails is reported instead of dropping the whole snapshot: a single
unsupported range must not take the charging state with it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .data import ConnectionState, DeviceIdentity, WallboxData
from .device.client import MennekesModbusClient
from .device.client_errors import AmtronBusError
from .device.const import DEFAULT_MEASUREMENT_INTERVAL_SECONDS, DOMAIN
from .device.poller import DevicePoller
from .device.registers import ERROR_CODE
from .device.runtime_events import EventTimeline
from .device.waveshare_auth import WaveshareAuthenticationRejectedError
from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)


class MennekesAmtronCoordinator(DataUpdateCoordinator[WallboxData]):
    """Read the supported register blocks on a fixed interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MennekesAmtronConfigEntry,
        *,
        client: MennekesModbusClient,
        identity: DeviceIdentity,
        connection_state: ConnectionState,
        scan_interval: float,
        measurement_interval: float = DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
        timeline: EventTimeline | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"MENNEKES AMTRON {client.config.port}",
            update_interval=timedelta(seconds=scan_interval),
            always_update=False,
        )
        self._client = client
        self._entry = entry
        self._identity = identity
        self.control_status: Callable[[], tuple[str | None, datetime | None]] | None = None
        self.observe_recovery: Callable[[object, int], None] | None = None
        self.observe_goal: Callable[[WallboxData | None], None] | None = None
        self.observe_plug_charge: Callable[[WallboxData | None, datetime | None], None] | None = None
        self.retry_soc_goal: Callable[[], None] | None = None
        self.observe_session: Callable[[WallboxData | None], Awaitable[None]] | None = None
        self._poller = DevicePoller(
            client,
            identity,
            connection_state=connection_state,
            measurement_interval=measurement_interval,
            timeline=timeline,
            monotonic=monotonic,
        )

    @property
    def identity(self) -> DeviceIdentity:
        """Return the device facts read during setup."""

        return self._identity

    def current_data(self) -> WallboxData:
        """Return the newest snapshot, or an empty one before the first poll."""

        return self.data or WallboxData()

    async def _async_update_data(self) -> WallboxData:
        goal_generation = self._client.write_state.energy_goal_generation
        observed_at = datetime.now(UTC)
        try:
            data = await self._poller.async_poll(
                self.current_data(), last_success=self.last_update_success
            )
            if self.observe_plug_charge is not None:
                self.observe_plug_charge(data, observed_at)
            if self.control_status is not None:
                data.pending_change, data.pending_change_until = self.control_status()
            if self.observe_recovery is not None:
                self.observe_recovery(
                    data.values.get(ERROR_CODE.key), data.current_write_generation,
                )
            if self.observe_goal is not None:
                self.observe_goal(data)
            if self.retry_soc_goal is not None:
                self.retry_soc_goal()
            if self.observe_session is not None:
                await self.observe_session(data)
            return data
        except AmtronBusError as err:
            if self.observe_plug_charge is not None:
                self.observe_plug_charge(None, None)
            if self.observe_session is not None:
                await self.observe_session(None)
            if self.observe_goal is not None:
                self.observe_goal(WallboxData(
                    energy_read=True, energy_goal_generation=goal_generation,
                ))
            if isinstance(err, WaveshareAuthenticationRejectedError):
                auth_error = ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="gateway_authentication_failed"
                )
                # HA suppresses listener notifications for two consecutive
                # failed polls. Publish this distinct reason before stopping
                # heartbeat so an earlier connectivity repair can clear too.
                was_available = self.last_update_success
                self.async_set_update_error(auth_error)
                if not was_available:
                    self.async_update_listeners()
                if (heartbeat := self._entry.runtime_data.heartbeat) is not None:
                    await heartbeat.async_stop()
                raise auth_error from err
            raise UpdateFailed(str(err)) from err
