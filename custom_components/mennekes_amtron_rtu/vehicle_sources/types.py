"""Small local-data contracts used by charging features and configuration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from homeassistant.core import CALLBACK_TYPE

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)

Capability = Literal["departure", "charge_history"]


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """Only capabilities implemented by an installed adapter are advertised."""

    id: str
    integration_domain: str
    capabilities: frozenset[Capability]


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """A provider and its selected HA device, without manufacturer identities."""

    provider_id: str
    device_id: str


@dataclass(frozen=True, slots=True)
class HistoryMatch:
    """A complete matching history interval, with estimated battery energy."""

    vehicle_kwh: float
    records: int


class DepartureSource(Protocol):
    """Read the next departure from already available local HA data."""

    @property
    def available(self) -> bool: ...

    def next_departure(self, now: datetime) -> datetime | None: ...


class ChargeHistorySource(Protocol):
    """Bind a local history source to one vehicle for one observer lifetime."""

    @property
    def source_key(self) -> str: ...

    @property
    def available(self) -> bool: ...

    async def async_match(self, session: CompletedSession) -> HistoryMatch | None: ...

    def subscribe(self, on_change: Callable[[], None]) -> CALLBACK_TYPE: ...
