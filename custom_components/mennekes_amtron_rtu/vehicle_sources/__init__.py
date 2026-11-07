"""Optional vehicle integrations, behind manufacturer-independent capabilities."""

from .registry import (
    loss_source,
    provider_definitions,
    provider_device_is_valid,
    provider_selection,
    resolve_departure_source,
    resolve_history_source,
)
from .types import (
    Capability,
    ChargeHistorySource,
    DepartureSource,
    HistoryMatch,
    ProviderDefinition,
    ProviderSelection,
)

__all__ = [
    "Capability", "ChargeHistorySource", "DepartureSource", "HistoryMatch",
    "ProviderDefinition", "ProviderSelection", "loss_source", "provider_definitions",
    "provider_device_is_valid", "provider_selection", "resolve_departure_source",
    "resolve_history_source",
]
