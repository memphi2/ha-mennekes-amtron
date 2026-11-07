"""Optional vehicle integrations, behind manufacturer-independent capabilities."""

from .registry import (
    loss_source,
    profile_capabilities,
    provider_definitions,
    provider_device_is_valid,
    provider_identity,
    provider_selection,
    resolve_departure_source,
    resolve_history_source,
    resolve_telemetry_source,
)
from .types import (
    Capability,
    ChargeHistorySource,
    DepartureSource,
    HistoryMatch,
    ProviderDefinition,
    ProviderSelection,
    VehicleTelemetry,
    VehicleTelemetrySource,
)

__all__ = [
    "Capability",
    "ChargeHistorySource",
    "DepartureSource",
    "HistoryMatch",
    "ProviderDefinition",
    "ProviderSelection",
    "VehicleTelemetry",
    "VehicleTelemetrySource",
    "loss_source",
    "profile_capabilities",
    "provider_definitions",
    "provider_device_is_valid",
    "provider_identity",
    "provider_selection",
    "resolve_departure_source",
    "resolve_history_source",
    "resolve_telemetry_source",
]
