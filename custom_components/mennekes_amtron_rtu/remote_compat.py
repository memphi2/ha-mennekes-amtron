"""HA-side requirements for independently released Modbus services."""

from __future__ import annotations

from collections.abc import Mapping

from packaging.version import Version

from .remote_errors import ServiceConnectionError

SUPPORTED_API_VERSION = 2
MIN_SERVICE_VERSION = "0.6.1"
# Released API 2 initially had no service_version field. This is a fixed known
# baseline, never the running integration's minimum or latest service version.
LEGACY_API2_SERVICE_VERSION = "0.5.0"


def validate_service_version(snapshot: Mapping[str, object]) -> None:
    """Check a snapshot whose API version has already been accepted as API 2."""
    raw_version = snapshot.get("service_version", LEGACY_API2_SERVICE_VERSION)
    if not isinstance(raw_version, str) or not 1 <= len(raw_version) <= 128:
        raise ValueError("invalid service version")
    version = Version(raw_version)
    if version < Version(MIN_SERVICE_VERSION):
        raise ServiceConnectionError(
            "service_version_mismatch", "service version is below the required minimum"
        )
