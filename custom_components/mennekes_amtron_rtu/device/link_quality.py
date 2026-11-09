"""Stable communication quality plus a failure count, never per-transaction state."""

from __future__ import annotations

import time
from collections.abc import Callable

from .client_errors import (
    AmtronConnectionError,
    AmtronIdentityError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
    AmtronWriteRejectedError,
)

CONNECTION_QUALITY_STATES = ("good", "degraded")
QUALITY_RECOVERY_SECONDS = 300.0


class LinkQuality:
    """Latch an observed communication failure until five clean minutes pass.

    A successful transaction after the quiet period is required to recover;
    time passing without traffic never proves that communication improved.
    Two timestamps and two counters are retained, independent of traffic
    volume. A failure is a transaction that finally failed; a retry is a lost
    reply that a later attempt made good. Only failures degrade the link. The
    counters change only on these events, so a clean bus never produces
    state updates.
    """

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._last_failure: float | None = None
        self._last_success: float | None = None
        self.failures = 0
        self.retries = 0

    @property
    def state(self) -> str | None:
        """Return unknown before any relevant result, otherwise a stable enum."""
        if self._last_failure is not None and (
            self._last_success is None
            or self._last_success - self._last_failure < QUALITY_RECOVERY_SECONDS
        ):
            return "degraded"
        return "good" if self._last_success is not None else None

    def observe_retry(self) -> None:
        """Count a lost reply that is retried; the retry's result decides."""
        self.retries += 1

    def observe(self, error: BaseException | None = None, *, retried: bool = False) -> None:
        """Observe bus results, excluding unsupported/configuration requests.

        A communication failure of a request its caller resends counts as a
        retry; the resend's own result decides about the link.
        """
        if error is None or (
            isinstance(error, AmtronProtocolError) and error.exception_code is not None
        ):
            self._last_success = self._monotonic()
        elif isinstance(error, (AmtronConnectionError, AmtronProtocolError)) and not isinstance(
            error, (AmtronIdentityError, AmtronIllegalAddressError, AmtronWriteRejectedError)
        ):
            if retried:
                self.retries += 1
                return
            self._last_failure = self._monotonic()
            self.failures += 1
