"""The energy-manager heartbeat task.

The wallbox expects 0x0D00 = 0x55AA at least every ten seconds while this
integration acts as the Modbus master. Without it the device turns into error
state 200 when no EV is connected and no fallback current is configured.

The heartbeat therefore runs in its own task and writes nothing else: a slow
or failing data poll must never be able to starve it, which is exactly the
documented way into that error state.

Own task is not enough on its own, though, because the bus is shared. Two
things follow from that. The write goes out as a priority transaction, so it
waits for at most the one transfer already in flight. And the task spends the
interval rather than adding it: it sleeps what is left after the write, and
says so in the log when the device was still left waiting longer than it
allows.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

from .client import MennekesModbusClient
from .client_errors import AmtronBusError
from .const import (
    HEARTBEAT_DEADLINE_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_RETRY_INTERVAL_SECONDS,
    HEARTBEAT_VALUE,
)
from .data import WriteDiagnostics
from .registers import HEARTBEAT
from .runtime_events import EventType

_LOGGER = logging.getLogger(__name__)

class HeartbeatTask:
    """Periodic heartbeat writer for one wallbox."""

    def __init__(
        self,
        client: MennekesModbusClient,
        diagnostics: WriteDiagnostics,
        *,
        interval: float = HEARTBEAT_INTERVAL_SECONDS,
        deadline: float = HEARTBEAT_DEADLINE_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._diagnostics = diagnostics
        self._interval = interval
        self._deadline = deadline
        self._sleep = sleep
        self._monotonic = monotonic
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._delivered_at: float | None = None
        self._failed = False
        self._healthy: bool | None = None
        self._deadline_handle: asyncio.TimerHandle | None = None
        self._listeners: set[Callable[[], None]] = set()

    @property
    def healthy(self) -> bool | None:
        """Whether this task has a confirmed heartbeat inside its deadline."""

        return self._healthy

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to health transitions, never to individual beats."""

        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _set_healthy(self, healthy: bool | None) -> None:
        if self._healthy is healthy:
            return
        self._healthy = healthy
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                # A UI observer must never kill the time-critical writer.
                _LOGGER.exception("Heartbeat status listener failed")

    def _cancel_deadline(self) -> None:
        if self._deadline_handle is not None:
            self._deadline_handle.cancel()
            self._deadline_handle = None

    def _arm_deadline(self) -> None:
        self._cancel_deadline()
        self._deadline_handle = asyncio.get_running_loop().call_later(
            self._deadline, self._deadline_expired
        )

    def _deadline_expired(self) -> None:
        self._deadline_handle = None
        self._set_healthy(False)

    @property
    def running(self) -> bool:
        """Return true while the heartbeat task is alive."""

        return self._task is not None and not self._task.done()

    async def async_beat_once(self) -> bool:
        """Write one heartbeat and return whether it reached the device."""

        try:
            await self._client.async_write_register(
                HEARTBEAT, HEARTBEAT_VALUE, priority=True
            )
        except AmtronBusError as err:
            if not self._failed:
                self._diagnostics.timeline.record(EventType.HEARTBEAT_FAILED)
            self._failed = True
            self._diagnostics.heartbeats_failed += 1
            self._diagnostics.last_error = str(err)
            _LOGGER.debug("Heartbeat write failed: %s", err)
            return False
        self._diagnostics.heartbeats_sent += 1
        if self._failed:
            self._diagnostics.timeline.record(EventType.HEARTBEAT_RECOVERED)
            self._failed = False
        return True

    async def async_run(self) -> None:
        """Write the heartbeat until the task is cancelled.

        What is slept is the remainder of the interval, not the interval. A
        write that waited for the bus lock has already spent part of the
        budget, and adding a full interval on top of it is how a busy bus
        walks past the deadline the device enforces.
        """

        self._set_healthy(None)
        self._delivered_at = None
        self._arm_deadline()
        try:
            while not self._stopping:
                started = self._monotonic()
                delivered = await self.async_beat_once()
                finished = self._monotonic()
                if delivered:
                    self._note_delivery(finished)
                    self._set_healthy(True)
                    self._arm_deadline()
                # Only this idempotent heartbeat gets a shorter retry cadence.
                interval = self._interval if delivered else min(
                    self._interval, HEARTBEAT_RETRY_INTERVAL_SECONDS
                )
                await self._sleep(max(0.0, interval - (finished - started)))
        finally:
            self._cancel_deadline()
            self._set_healthy(False)

    def start(self) -> None:
        """Start the heartbeat as an asyncio task."""

        if self.running:
            return
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(
            self.async_run(), name="mennekes_amtron_rtu_heartbeat"
        )

    def attach(self, task: asyncio.Task[None]) -> None:
        """Adopt a task created by Home Assistant's entry task helper."""

        self._stopping = False
        self._task = task

    async def async_stop(self) -> None:
        """Cancel the heartbeat task and wait for it to finish."""

        self._stopping = True
        self._cancel_deadline()
        self._set_healthy(False)
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _note_delivery(self, now: float) -> None:
        """Record a delivered heartbeat and report a missed deadline.

        A failed write never reached the device, so only delivered beats
        count towards the gap the wallbox actually saw.
        """

        previous = self._delivered_at
        self._delivered_at = now
        if previous is None:
            return
        gap = now - previous
        if gap <= self._deadline:
            return
        self._diagnostics.heartbeats_late += 1
        self._diagnostics.timeline.record(EventType.HEARTBEAT_LATE)
        _LOGGER.warning(
            "The heartbeat reached %s %.1f s apart, more than the %.0f s the "
            "wallbox allows; it may report the energy-manager error",
            self._client.config.port,
            gap,
            self._deadline,
        )
