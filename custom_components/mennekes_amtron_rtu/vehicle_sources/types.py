"""Small local-data contracts used by charging features and configuration."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from homeassistant.core import CALLBACK_TYPE

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)

Capability = Literal["soc", "charge_limit", "capacity", "departure", "charge_history", "detection"]

DETECTION_MAX_AGE_SECONDS = 1800
# A car's own plug report and the wallbox's poll come from two clocks. A report
# this close to the plug-in belongs to it.
PLUG_WINDOW = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class VehicleDetection:
    """Positive or negative local evidence; missing evidence stays unknown."""

    connected: bool | None = None
    home: bool | None = None
    # Evidence age matters for grants, but does not change automatic selection.
    connected_at: datetime | None = field(default=None, compare=False)
    # The last known location, however old: a parked car reports none.
    parked_home: bool | None = field(default=None, compare=False)
    # When the cable signal switched to its current state; a repeated report
    # does not move it.
    changed_at: datetime | None = field(default=None, compare=False)

    def at_home(self, plugged_at: datetime | None) -> bool | None:
        """Whether this car reports itself plugged in at home.

        A stale location counts only when the car's cable signal switched to
        plugged in together with the wallbox, so a car plugged in long after
        parking is recognized.
        """
        if self.connected is False or self.home is False:
            return False
        if self.connected is not True:
            return None
        if self.home is True:
            return True
        if (
            self.parked_home is True and plugged_at is not None and self.changed_at is not None
            and abs(self.changed_at - plugged_at) <= PLUG_WINDOW
        ):
            return True
        return None


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
    # Since when the source reports this charge level; only a direct read of
    # the source carries it.
    soc_reported_at: datetime | None = field(default=None, kw_only=True)


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
