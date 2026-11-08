"""Home Assistant runtime ownership, separate from the device driver."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .device.data import ConnectionState as ConnectionState
from .device.data import DeviceIdentity as DeviceIdentity
from .device.data import WallboxData as WallboxData
from .device.data import WriteDiagnostics as WriteDiagnostics

if TYPE_CHECKING:
    from homeassistant.const import Platform

    from .charge_loss_observer import ChargeLossObserver
    from .control import AmtronControl
    from .coordinator import MennekesAmtronCoordinator
    from .cost_observer import ChargingCostObserver
    from .device.client import MennekesModbusClient
    from .device.energy_goal_controller import EnergyGoalController
    from .device.heartbeat import HeartbeatTask
    from .device.soc_goal_controller import SocGoalController
    from .plug_charge import PlugChargeObserver
    from .recovery import HeartbeatRecovery
    from .session_observer import SessionObserver
    from .timed_goal_observer import TimedGoalObserver
    from .vehicle_detection import VehicleDetectionObserver
    from .vehicle_soc import VehicleSocObserver
    from .vehicle_sources import VehicleTelemetrySource


@dataclass(slots=True)
class MennekesAmtronRuntimeData:
    """Everything one loaded config entry owns."""

    client: MennekesModbusClient
    coordinator: MennekesAmtronCoordinator
    control: AmtronControl
    identity: DeviceIdentity
    diagnostics: WriteDiagnostics
    connection_state: ConnectionState
    retain_client_on_unload: bool = False
    retain_cost_on_unload: bool = False
    retain_attribution_on_unload: bool = False
    entry_data: dict[str, Any] = field(default_factory=dict)
    loss_attribution_unsubscribe: Callable[[], None] | None = None
    # Config data/options are already replaced when an update starts unloading.
    # Retain this runtime's platform set instead of recomputing from the new mode.
    loaded_platforms: tuple[Platform, ...] = ()
    # The settings and vehicle the running observers were started for. A
    # change of the selection alone switches vehicles without a reload.
    vehicle_base_options: dict[str, Any] = field(default_factory=dict)
    vehicle_selection: dict[str, Any] = field(default_factory=dict)
    vehicle_profile_id: str | None = None
    vehicle_switching: bool = False
    heartbeat: HeartbeatTask | None = None
    recovery: HeartbeatRecovery | None = None
    goal: EnergyGoalController | None = None
    soc_goal: SocGoalController | None = None
    vehicle_soc: VehicleSocObserver | None = None
    vehicle_telemetry: VehicleTelemetrySource | None = None
    vehicle_detection: VehicleDetectionObserver | None = None
    plug_charge: PlugChargeObserver | None = None
    timed_goal: TimedGoalObserver | None = None
    sessions: SessionObserver | None = None
    charge_loss: ChargeLossObserver | None = None
    charging_cost: ChargingCostObserver | None = None
    # What the repair issues looked like last time, so an unchanged issue is
    # not written to the issue registry on every poll.
    repair_state: dict[str, dict[str, str] | None] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeHandoff:
    """Owners retained only for the duration of a controlled entry reload."""

    identity: DeviceIdentity
    unloaded: bool = False
    setup_complete: bool = False
    connection_generation: int | None = None
    client: MennekesModbusClient | None = None
    sessions: SessionObserver | None = None
    charging_cost: ChargingCostObserver | None = None

    async def async_close(self) -> None:
        """Release anything not claimed by the replacement runtime."""
        sessions, cost, client = self.sessions, self.charging_cost, self.client
        self.sessions = self.charging_cost = self.client = None
        try:
            if sessions is not None:
                await sessions.async_close()
        finally:
            try:
                if cost is not None:
                    await cost.async_stop()
            finally:
                if client is not None:
                    await client.async_close()
