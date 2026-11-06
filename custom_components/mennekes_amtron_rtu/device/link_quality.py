"""Stable communication quality without counters or per-transaction HA state."""

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
    Only two timestamps are retained, independent of traffic volume.
    """

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._last_failure: float | None = None
        self._last_success: float | None = None

    @property
    def state(self) -> str | None:
        """Return unknown before any relevant result, otherwise a stable enum."""
        if self._last_failure is not None and (
            self._last_success is None
            or self._last_success - self._last_failure < QUALITY_RECOVERY_SECONDS
        ):
            return "degraded"
        return "good" if self._last_success is not None else None

    def observe(self, error: BaseException | None = None) -> None:
        """Observe bus results, excluding unsupported/configuration requests."""
        if error is None or (
            isinstance(error, AmtronProtocolError) and error.exception_code is not None
        ):
            self._last_success = self._monotonic()
        elif isinstance(error, (AmtronConnectionError, AmtronProtocolError)) and not isinstance(
            error, (AmtronIdentityError, AmtronIllegalAddressError, AmtronWriteRejectedError)
        ):
            self._last_failure = self._monotonic()
