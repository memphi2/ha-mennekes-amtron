"""Small local-data contracts used by charging features and configuration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from homeassistant.core import CALLBACK_TYPE

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)

Capability = Literal["soc", "charge_limit", "capacity", "departure", "charge_history", "detection"]

DETECTION_MAX_AGE_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class VehicleDetection:
    """Positive or negative local evidence; missing evidence stays unknown."""

    connected: bool | None = None
    home: bool | None = None


class VehicleDetectionSource(Protocol):
    """Observe one vehicle's cable and location without contacting the car."""

    @property
    def source_key(self) -> str: ...

    @property
    def available(self) -> bool: ...

    @property
    def snapshot(self) -> VehicleDetection: ...

    def subscribe(self, on_change: Callable[[VehicleDetection], None]) -> CALLBACK_TYPE: ...


@dataclass(frozen=True, slots=True)
class VehicleTelemetry:
    """Validated optional values from one locally selected vehicle."""

    soc_percent: float | None = None
    maximum_percent: float | None = None
    capacity_kwh: float | None = None


class VehicleTelemetrySource(Protocol):
    """Observe already published vehicle data without polling its integration."""

    @property
    def source_key(self) -> str: ...

    @property
    def available(self) -> bool: ...

    @property
    def snapshot(self) -> VehicleTelemetry: ...

    def subscribe(self, on_change: Callable[[VehicleTelemetry], None]) -> CALLBACK_TYPE: ...


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
