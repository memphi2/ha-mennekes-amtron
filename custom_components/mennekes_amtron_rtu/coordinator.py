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
from .const import DEFAULT_MEASUREMENT_INTERVAL_SECONDS
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
        measurement_interval: float = DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
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
        self._active_read_at: float | None = None
        self._measurement_interval = measurement_interval

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
        return self._monotonic() - self._slow_read_at >= SLOW_BLOCK_INTERVAL_SECONDS

    async def _async_update_data(self) -> WallboxData:
        # Configuration registers change when somebody reconfigures the
        # wallbox, not while it charges, so they are carried forward between
        # the slow reads instead of occupying the bus every few seconds.
        slow_due = self._slow_blocks_are_due()
        active_due: bool | None = None
        values: dict[str, RegisterValue] = dict(self.current_data().values)
        read: dict[str, RegisterValue] = {}
        failed = set(self.current_data().failed_blocks)
        stale: list[str] = []
        # Read status first: a plug/start/stop transition must refresh the
        # measurements and final energy counters in this same poll.
        order = {BlockCadence.FAST: 0, BlockCadence.ACTIVE: 1, BlockCadence.SLOW: 2}
        blocks = sorted(
            supported_blocks(self._identity.layout_version),
            key=lambda block: order[block.cadence],
        )
        for block in blocks:
            state_changed = read.get("evse_state") != values.get("evse_state")
            if block.cadence is BlockCadence.SLOW and not (
                slow_due or (block.name == "statistics" and state_changed)
            ):
                continue
            if block.cadence is BlockCadence.ACTIVE:
                if active_due is None:
                    interval = (
                        max(SLOW_BLOCK_INTERVAL_SECONDS, self._measurement_interval)
                        if read.get("evse_state") == EvseState.IDLE
                        else self._measurement_interval
                    )
                    active_due = (
                        self._active_read_at is None
                        or state_changed
                        or not self.last_update_success
                        or self._monotonic() - self._active_read_at >= interval
                    )
                if not active_due:
                    continue
            failed.discard(block.name)
            try:
                read.update(await self._client.async_read_block(block))
            except AmtronConnectionError as err:
                self._log_unavailable(err)
                raise UpdateFailed(str(err)) from err
            except AmtronBusError as err:
                failed.add(block.name)
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
        if active_due:
            self._active_read_at = self._monotonic()
        for key in stale:
            values.pop(key, None)
        values.update(read)
        self._log_available()
        return WallboxData(values=values, failed_blocks=tuple(sorted(failed)))

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
