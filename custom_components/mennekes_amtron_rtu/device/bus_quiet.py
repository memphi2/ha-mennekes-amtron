"""Let the line fall silent after an unanswered request.

An RTU frame carries no transaction number. An answer that arrives after its
request timed out is therefore taken for the answer to whatever was asked
next, if that has the same shape, and every answer after it is one late.
After a request that got no usable answer, the next one waits until nothing
has arrived for one quiet period; what arrives meanwhile is dropped by the
library, which has no request open then. The wait is bounded, so a line that
is never silent still carries the heartbeat.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .client_errors import AmtronBusError, AmtronProtocolError

MAX_QUIET_SECONDS = 1.0


class BusQuiet:
    """Tracks whether an answer may still be on its way, and waits it out."""

    def __init__(self, *, quiet: float, enabled: bool = True) -> None:
        # A long reply timeout must not cost the heartbeat its deadline.
        self._quiet = min(quiet, MAX_QUIET_SECONDS)
        # Frames with a transaction number, or a line that is none, need no wait.
        self._enabled = enabled
        # Since when an answer nobody waits for may still arrive.
        self._suspect_since: float | None = None
        self._received_at = float("-inf")

    @staticmethod
    def _monotonic() -> float:
        # The loop's own clock: a controlled clock of the caller may stand still.
        return asyncio.get_running_loop().time()

    def packet(self, sending: bool, data: bytes) -> bytes:
        """Note every received chunk; bytes pass through untouched."""
        if not sending:
            # Only extends a wait that an unanswered request began: a stray
            # byte after a good answer is no answer out of step.
            self._received_at = self._monotonic()
        return data

    @property
    def suspect(self) -> bool:
        """Whether the next request would have to wait for silence first."""
        return self._enabled and self._suspect_since is not None

    def unanswered(self) -> None:
        """A request timed out or was answered with something else."""
        self._suspect_since = self._monotonic()

    async def settle(self) -> None:
        """Before a request: wait until the line was silent for one quiet period."""
        if self._suspect_since is None:
            return
        # Noise that never ends must not hold the line for good.
        latest = self._monotonic() + 2 * self._quiet
        while self._enabled:
            now = self._monotonic()
            silent_at = max(self._suspect_since, self._received_at) + self._quiet
            if now >= min(silent_at, latest):
                break
            await asyncio.sleep(min(silent_at, latest) - now)
        self._suspect_since = None

    async def ask[T](self, request: Callable[[], Awaitable[T]]) -> T:
        """Run one request with its retries, on a line known to be silent."""
        await self.settle()
        try:
            return await request()
        except AmtronProtocolError as err:
            if err.exception_code is None:
                # No answer, or not the answer to this request.
                self.unanswered()
            raise
        except (AmtronBusError, asyncio.CancelledError):
            # A lost connection or a cancelled wait: an answer may still come,
            # also on the same line once it is open again.
            self.unanswered()
            raise
