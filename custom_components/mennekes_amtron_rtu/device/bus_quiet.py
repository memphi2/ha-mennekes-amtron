"""Let the line fall silent after an unanswered request.

An RTU frame carries no transaction number. An answer that arrives after its
request timed out is therefore taken for the answer to whatever was asked
next, if that has the same shape, and every answer after it is one late.
After a request that got no usable answer, the next one waits until nothing
has arrived for one reply timeout; what arrives meanwhile is dropped by the
library, which has no request open then.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .client_errors import AmtronBusError, AmtronProtocolError


class BusQuiet:
    """Tracks whether an answer may still be on its way, and waits it out."""

    def __init__(self, *, quiet: float, enabled: bool = True) -> None:
        self._quiet = quiet
        # Frames with a transaction number, or a line that is none, need no wait.
        self._enabled = enabled
        # Since when an answer nobody waits for may still arrive.
        self._suspect_since: float | None = None
        self._received_at = float("-inf")
        self._asking = False

    @staticmethod
    def _monotonic() -> float:
        # The loop's own clock: a controlled clock of the caller may stand still.
        return asyncio.get_running_loop().time()

    def packet(self, sending: bool, data: bytes) -> bytes:
        """Note every received chunk; bytes pass through untouched."""
        if not sending:
            self._received_at = now = self._monotonic()
            if not self._asking:
                # Nobody asked: the answers on this line are out of step.
                self._suspect_since = now
        return data

    def unanswered(self) -> None:
        """A request timed out or was answered with something else."""
        self._suspect_since = self._monotonic()

    async def settle(self) -> None:
        """Before a request: wait until the line was silent for one reply timeout."""
        while self._suspect_since is not None:
            remaining = max(self._suspect_since, self._received_at) + self._quiet - self._monotonic()
            if remaining <= 0 or not self._enabled:
                self._suspect_since = None
                return
            await asyncio.sleep(remaining)

    async def ask[T](self, request: Callable[[], Awaitable[T]]) -> T:
        """Run one request with its retries, on a line known to be silent."""
        await self.settle()
        self._asking = True
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
        finally:
            self._asking = False
