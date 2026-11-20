"""Home Assistant runtime ownership, separate from the device driver."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.util.hass_dict import HassKey

from .device.const import DOMAIN
from .device.data import ConnectionState as ConnectionState
from .device.data import DeviceIdentity as DeviceIdentity
from .device.data import WallboxData as WallboxData

if TYPE_CHECKING:
    from homeassistant.const import Platform

    from .charge_loss_observer import ChargeLossObserver
    from .charge_watch import ChargeWatch
    from .coordinator import ServiceCoordinator
    from .cost_goal import CostGoalObserver
    from .cost_observer import ChargingCostObserver
    from .guest_charging import GuestCharging
    from .plug_charge import PlugChargeObserver
    from .service_client import ServiceClient
    from .service_control import ServiceControl, ServiceHeartbeat
    from .timed_goal_observer import TimedGoalObserver
    from .vehicle_binding import VehicleBinding
    from .vehicle_detection import VehicleDetectionObserver, VehicleEvidence
    from .vehicle_soc import VehicleSocObserver
    from .vehicle_sources import VehicleTelemetrySource


@dataclass(slots=True)
class MennekesAmtronRuntimeData:
    """Everything one loaded config entry owns."""

    client: ServiceClient
    coordinator: ServiceCoordinator
    control: ServiceControl
    guest: GuestCharging
    identity: DeviceIdentity
    # False for a direct connection set to read only: it shows no controls.
    controls: bool = True
    retain_cost_on_unload: bool = False
    entry_data: dict[str, Any] = field(default_factory=dict)
    charge_watch: ChargeWatch | None = None
    charge_watch_unsubscribe: Callable[[], None] | None = None
    # Config data/options are already replaced when an update starts unloading.
    # Retain this runtime's platform set instead of recomputing from the new mode.
    loaded_platforms: tuple[Platform, ...] = ()
    # The settings and vehicle the running observers were started for. A
    # change of the selection alone switches vehicles without a reload.
    vehicle_base_options: dict[str, Any] = field(default_factory=dict)
    vehicle_selection: dict[str, Any] = field(default_factory=dict)
    vehicle_profile_id: str | None = None
    vehicle_switching: bool = False
    heartbeat: ServiceHeartbeat | None = None
    vehicle_soc: VehicleSocObserver | None = None
    vehicle_telemetry: VehicleTelemetrySource | None = None
    vehicle_detection: VehicleDetectionObserver | None = None
    vehicle_binding: VehicleBinding | None = None
    vehicle_evidence: VehicleEvidence | None = None
    plug_charge: PlugChargeObserver | None = None
    timed_goal: TimedGoalObserver | None = None
    charge_loss: ChargeLossObserver | None = None
    charging_cost: ChargingCostObserver | None = None
    cost_goal: CostGoalObserver | None = None
    # What the repair issues looked like last time, so an unchanged issue is
    # not written to the issue registry on every poll.
    repair_state: dict[str, dict[str, str] | None] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeHandoff:
    """Owners retained only for the duration of a controlled entry reload."""

    identity: DeviceIdentity
    charging_cost: ChargingCostObserver | None = None

    async def async_close(self) -> None:
        """Release anything not claimed by the replacement runtime."""
        cost, self.charging_cost = self.charging_cost, None
        if cost is not None:
            await cost.async_stop()


# What a reload hands from the unloaded entry to its next setup, by entry ID.
RELOAD_HANDOFFS: HassKey[dict[str, RuntimeHandoff]] = HassKey(f"{DOMAIN}_reload_clients")
