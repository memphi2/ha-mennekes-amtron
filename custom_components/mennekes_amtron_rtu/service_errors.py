"""Safe service failure categories for connection setup and diagnostics."""

from __future__ import annotations

from typing import Literal

from .device.client_errors import AmtronConnectionError

type ServiceFailure = Literal[
    "service_auth_failed", "service_not_ready", "service_invalid_response",
    "service_identity_changed",
    "service_version_mismatch",
]


class ServiceConnectionError(AmtronConnectionError):
    """Keep authentication, readiness and invalid API data distinguishable."""

    def __init__(self, reason: ServiceFailure, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
