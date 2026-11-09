"""Cadence-aware polling shared by direct mode and the independent service."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable

from .capabilities import supported_blocks
from .client import MennekesModbusClient
from .client_errors import AmtronBusError, AmtronConnectionError, AmtronProtocolError
from .const import DEFAULT_MEASUREMENT_INTERVAL_SECONDS
from .data import ConnectionState, DeviceIdentity, WallboxData
from .decode import RegisterValue
from .enums import EvseState
from .register_blocks import SLOW_BLOCK_INTERVAL_SECONDS, BlockCadence
from .runtime_events import EventTimeline, EventType

_LOGGER = logging.getLogger(__name__)
_BLOCK_RETRY_INITIAL_SECONDS = 5.0
# A single disturbed bus transaction must not make every entity of a block
# flicker unavailable. Recently read status and measurements bridge it; the
# energy meter never does, as energy goals rely on its failure being visible.
_BLOCK_HOLD_SECONDS = 30.0
_NEVER_HELD = frozenset({"statistics"})
# Vehicle presence depends on these values; losing them must be visible.
_PRESENCE_KEYS = frozenset({"evse_state", "cp_state"})


class DevicePoller:
    """Poll the single bus owner without a Home Assistant dependency."""

    def __init__(
        self,
        client: MennekesModbusClient,
        identity: DeviceIdentity,
        *,
        connection_state: ConnectionState,
        measurement_interval: float = DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
        timeline: EventTimeline | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._identity = identity
        self._connection_state = connection_state
        self._measurement_interval = measurement_interval
        self._timeline = timeline
        self._monotonic = monotonic
        self._slow_read_at: float | None = None
        self._active_read_at: float | None = None
        # Retry only the failed block, with bounded backoff. A single invalid
        # meter reading must not wait for the next complete slow poll, while an
        # unsupported register must not occupy the bus on every status poll.
        self._block_retries: dict[str, tuple[float, float]] = {}
        self._block_read_at: dict[str, float] = {}
        self._lost_blocks: set[str] = set()

    def _retry_due(self, name: str) -> bool:
        retry = self._block_retries.get(name)
        return retry is not None and self._monotonic() >= retry[0]

    def _retry_later(self, name: str) -> None:
        previous = self._block_retries.get(name)
        delay = (
            _BLOCK_RETRY_INITIAL_SECONDS if previous is None
            else min(previous[1] * 2, SLOW_BLOCK_INTERVAL_SECONDS)
        )
        self._block_retries[name] = (self._monotonic() + delay, delay)

    def _slow_blocks_are_due(self) -> bool:
        """Return whether the configuration blocks should be read again."""

        if self._slow_read_at is None:
            return True
        return self._monotonic() - self._slow_read_at >= SLOW_BLOCK_INTERVAL_SECONDS

    async def async_poll(
        self, previous: WallboxData, *, last_success: bool
    ) -> WallboxData:
        # A poll spans multiple bus transactions. If a current write overlaps
        # any part of it, its old readback must not undo the accepted command.
        generation = self._client.write_state.current_write_generation
        goal_generation = self._client.write_state.energy_goal_generation
        # Configuration registers change when somebody reconfigures the
        # wallbox, not while it charges, so they are carried forward between
        # the slow reads instead of occupying the bus every few seconds.
        slow_due = self._slow_blocks_are_due()
        active_due: bool | None = None
        values: dict[str, RegisterValue] = dict(previous.values)
        read: dict[str, RegisterValue] = {}
        failed = set(previous.failed_blocks)
        stale: list[str] = []
        held: set[str] = set()
        answered = False
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
                or (block.name == "statistics" and not last_success)
                or self._retry_due(block.name)
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
                        or not last_success
                        or self._monotonic() - self._active_read_at >= interval
                    )
                if not active_due and not self._retry_due(block.name):
                    continue
            failed.discard(block.name)
            try:
                result = await self._client.async_read_block(block)
            except AmtronConnectionError as err:
                self._log_unavailable(err)
                raise
            except AmtronBusError as err:
                failed.add(block.name)
                # A valid Modbus exception proves the device answered even
                # though it did not supply this block's data.
                answered |= isinstance(err, AmtronProtocolError) and err.exception_code is not None
                # Carrying a block forward is otherwise only right while it was
                # not due. A block that keeps failing has no current value, and
                # a frozen measurement an automation still believes is worse
                # than an entity that admits it knows nothing.
                read_at = self._block_read_at.get(block.name)
                if (
                    block.name in _NEVER_HELD or read_at is None
                    or self._monotonic() - read_at > _BLOCK_HOLD_SECONDS
                ):
                    stale.extend(block.keys)
                    if _PRESENCE_KEYS & set(block.keys) and block.name not in self._lost_blocks:
                        self._lost_blocks.add(block.name)
                        _LOGGER.warning(
                            "Block %s unreadable for over %.0f seconds (%s); vehicle state unknown",
                            block.name, _BLOCK_HOLD_SECONDS, type(err).__name__,
                        )
                else:
                    held.update(block.keys)
                if block.cadence is not BlockCadence.FAST:
                    self._retry_later(block.name)
                _LOGGER.debug("Block %s failed: %s", block.name, err)
            else:
                read.update(result)
                self._block_read_at[block.name] = self._monotonic()
                if block.name in self._lost_blocks:
                    self._lost_blocks.discard(block.name)
                    _LOGGER.info("Block %s readable again", block.name)
                total = result.get("energy_total") if block.name == "statistics" else None
                invalid_meter = block.name == "statistics" and (
                    isinstance(total, bool) or not isinstance(total, (int, float))
                    or not math.isfinite(total) or total < 0
                )
                if invalid_meter:
                    self._retry_later(block.name)
                else:
                    self._block_retries.pop(block.name, None)
        if not read:
            error = AmtronConnectionError("no register block could be read")
            if not answered:
                self._log_unavailable(error)
            raise error
        if slow_due:
            self._slow_read_at = self._monotonic()
        if active_due:
            self._active_read_at = self._monotonic()
        for key in stale:
            values.pop(key, None)
        values.update(read)
        self._log_available()
        return WallboxData(
            values=values,
            failed_blocks=tuple(sorted(failed)),
            held=frozenset(held - read.keys()),
            current_write_generation=generation,
            connection_quality=self._client.link_quality.state,
            connection_failures=self._client.link_quality.failures,
            connection_retries=self._client.link_quality.retries,
            energy_read="energy_total" in read or "energy_total" in stale,
            energy_goal_generation=goal_generation,
        )

    def _log_unavailable(self, err: Exception) -> None:
        changed = self._connection_state.record(False)
        if changed and self._timeline is not None:
            self._timeline.record(EventType.CONNECTION_LOST)
        if changed and not self._connection_state.logged_unavailable:
            _LOGGER.warning(
                "Lost the connection to %s: %s", self._client.config.port, str(err)
            )
            self._connection_state.logged_unavailable = True

    def _log_available(self) -> None:
        changed = self._connection_state.record(True)
        if changed and self._timeline is not None:
            self._timeline.record(EventType.CONNECTION_RECOVERED)
        if changed and self._connection_state.logged_unavailable:
            _LOGGER.info("Reconnected to %s", self._client.config.port)
            self._connection_state.logged_unavailable = False
