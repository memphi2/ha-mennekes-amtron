"""Home Assistant runtime ownership, separate from the device driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
    from .recovery import HeartbeatRecovery
    from .session_observer import SessionObserver
    from .timed_goal_observer import TimedGoalObserver
    from .vehicle_soc import VehicleSocObserver


@dataclass(slots=True)
class MennekesAmtronRuntimeData:
    """Everything one loaded config entry owns."""

    client: MennekesModbusClient
    coordinator: MennekesAmtronCoordinator
    control: AmtronControl
    identity: DeviceIdentity
    diagnostics: WriteDiagnostics
    connection_state: ConnectionState
    # Config data/options are already replaced when an update starts unloading.
    # Retain this runtime's platform set instead of recomputing from the new mode.
    loaded_platforms: tuple[Platform, ...] = ()
    heartbeat: HeartbeatTask | None = None
    recovery: HeartbeatRecovery | None = None
    goal: EnergyGoalController | None = None
    soc_goal: SocGoalController | None = None
    vehicle_soc: VehicleSocObserver | None = None
    timed_goal: TimedGoalObserver | None = None
    sessions: SessionObserver | None = None
    charge_loss: ChargeLossObserver | None = None
    charging_cost: ChargingCostObserver | None = None
    # What the repair issues looked like last time, so an unchanged issue is
    # not written to the issue registry on every poll.
    repair_state: dict[str, dict[str, str] | None] = field(default_factory=dict)
