"""The energy-manager heartbeat task.

The wallbox expects 0x0D00 = 0x55AA at least every ten seconds while this
integration acts as the Modbus master. Without it the device turns into error
state 200 when no EV is connected and no fallback current is configured.

The heartbeat therefore runs in its own task and writes nothing else: a slow
or failing data poll must never be able to starve it, which is exactly the
documented way into that error state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from .client import MennekesModbusClient
from .client_errors import AmtronBusError
from .const import HEARTBEAT_INTERVAL_SECONDS, HEARTBEAT_VALUE
from .data import WriteDiagnostics
from .registers import HEARTBEAT

_LOGGER = logging.getLogger(__name__)

type TaskFactory = Callable[[Awaitable[None], str], object]


class HeartbeatTask:
    """Periodic heartbeat writer for one wallbox."""

    def __init__(
        self,
        client: MennekesModbusClient,
        diagnostics: WriteDiagnostics,
        *,
        interval: float = HEARTBEAT_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._diagnostics = diagnostics
        self._interval = interval
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        """Return true while the heartbeat task is alive."""

        return self._task is not None and not self._task.done()

    async def async_beat_once(self) -> bool:
        """Write one heartbeat and return whether it reached the device."""

        try:
            await self._client.async_write_register(HEARTBEAT, HEARTBEAT_VALUE)
        except AmtronBusError as err:
            self._diagnostics.heartbeats_failed += 1
            self._diagnostics.last_error = str(err)
            _LOGGER.debug("Heartbeat write failed: %s", err)
            return False
        self._diagnostics.heartbeats_sent += 1
        return True

    async def async_run(self) -> None:
        """Write the heartbeat until the task is cancelled."""

        while not self._stopping:
            await self.async_beat_once()
            await self._sleep(self._interval)

    def start(self) -> None:
        """Start the heartbeat as an asyncio task."""

        if self.running:
            return
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(
            self.async_run(), name="mennekes_amtron_heartbeat"
        )

    def attach(self, task: asyncio.Task[None]) -> None:
        """Adopt a task created by Home Assistant's entry task helper."""

        self._stopping = False
        self._task = task

    async def async_stop(self) -> None:
        """Cancel the heartbeat task and wait for it to finish."""

        self._stopping = True
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
