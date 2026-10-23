"""Polling coordinator for one AMTRON wallbox.

The wallbox has no push channel, so the integration polls. Only the blocks
the device's register layout actually supports are read, and a block that
fails is reported instead of dropping the whole snapshot: a single
unsupported range must not take the charging state with it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .capabilities import supported_blocks
from .client import MennekesModbusClient
from .client_errors import AmtronBusError, AmtronConnectionError
from .data import ConnectionState, DeviceIdentity, WallboxData
from .decode import RegisterValue
from .entry_types import MennekesAmtronConfigEntry
from .enums import EvseState
from .register_blocks import SLOW_BLOCK_INTERVAL_SECONDS, BlockCadence

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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"MENNEKES AMTRON {client.config.port}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self._client = client
        self._identity = identity
        self._connection_state = connection_state
        self._monotonic = monotonic
        self._slow_read_at: float | None = None

    @property
    def identity(self) -> DeviceIdentity:
        """Return the device facts read during setup."""

        return self._identity

    def current_data(self) -> WallboxData:
        """Return the newest snapshot, or an empty one before the first poll."""

        return self.data or WallboxData()

    def _slow_blocks_are_due(self) -> bool:
        """Return whether the configuration blocks should be read again."""

        if self._slow_read_at is None:
            return True
        return (
            self._monotonic() - self._slow_read_at >= SLOW_BLOCK_INTERVAL_SECONDS
        )

    def _vehicle_is_connected(self) -> bool:
        """Return whether anything is plugged into the wallbox.

        Only the one state that means "nothing is connected" demotes the
        measurement blocks. An unknown or unreadable state reads as connected,
        because being wrong in that direction only costs bus traffic, while
        being wrong in the other direction would hide a running charge.
        """

        return self.current_data().get("evse_state") != EvseState.IDLE

    async def _async_update_data(self) -> WallboxData:
        # Configuration registers change when somebody reconfigures the
        # wallbox, not while it charges, so they are carried forward between
        # the slow reads instead of occupying the bus every few seconds.
        slow_due = self._slow_blocks_are_due()
        # The measurement blocks describe a vehicle. Without one they report
        # zeroes and a drifting mains voltage, and reading them every few
        # seconds turns that into a database row every few seconds.
        active_due = slow_due or self._vehicle_is_connected()
        values: dict[str, RegisterValue] = dict(self.current_data().values)
        read: dict[str, RegisterValue] = {}
        failed: list[str] = []
        stale: list[str] = []
        for block in supported_blocks(self._identity.layout_version):
            if block.cadence is BlockCadence.SLOW and not slow_due:
                continue
            if block.cadence is BlockCadence.ACTIVE and not active_due:
                continue
            try:
                read.update(await self._client.async_read_block(block))
            except AmtronConnectionError as err:
                self._log_unavailable(err)
                raise UpdateFailed(str(err)) from err
            except AmtronBusError as err:
                failed.append(block.name)
                # Carrying a block forward is only right while it was not due.
                # A block that was read and failed has no current value, and a
                # frozen measurement an automation still believes is worse than
                # an entity that admits it knows nothing.
                stale.extend(block.keys)
                _LOGGER.debug("Block %s failed: %s", block.name, err)
        if not read:
            raise UpdateFailed("no register block could be read")
        if slow_due:
            self._slow_read_at = self._monotonic()
        for key in stale:
            values.pop(key, None)
        values.update(read)
        self._log_available()
        return WallboxData(values=values, failed_blocks=tuple(failed))

    def _log_unavailable(self, err: Exception) -> None:
        changed = self._connection_state.record(False)
        if changed and not self._connection_state.logged_unavailable:
            _LOGGER.warning(
                "Lost the connection to %s: %s", self._client.config.port, err
            )
            self._connection_state.logged_unavailable = True

    def _log_available(self) -> None:
        changed = self._connection_state.record(True)
        if changed and self._connection_state.logged_unavailable:
            _LOGGER.info("Reconnected to %s", self._client.config.port)
            self._connection_state.logged_unavailable = False
