"""Small in-memory write state that survives config-entry reloads.

No clients, tasks or HA entities are retained, and nothing is sent to Recorder.
A full HA restart deliberately starts a fresh conservative guard period.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._client_write import WriteRateLimiter
from .energy_goal import EnergyGoal
from .registers import CHARGING_CURRENT_EMS, REQUESTED_PHASES
from .soc_goal import SocGoal


@dataclass(slots=True)
class WriteState:
    """Timing shared by replacement clients and controllers for one entry."""

    limiter: WriteRateLimiter = field(default_factory=WriteRateLimiter)
    # Last actual/uncertain pause, resume, phase, release or lock transition.
    # Deferred commands live only on their controller and never survive unload.
    mode_change_at: float | None = None
    confirmed_current: float | None = None
    charging_setpoint: float | None = None
    charging_release_requested: bool | None = None
    pause_requested: bool | None = None
    current_write_generation: int = 0
    command_intent_generation: int = 0
    energy_goal_generation: int = 0
    energy_goal: EnergyGoal = field(default_factory=EnergyGoal)
    soc_goal_generation: int = 0
    soc_goal: SocGoal = field(default_factory=SocGoal)

    @classmethod
    def cold_start(cls, now: float) -> WriteState:
        """Guard writes whose history is unknown after a process restart."""

        state = cls(mode_change_at=now)
        for spec in (CHARGING_CURRENT_EMS, REQUESTED_PHASES):
            state.limiter.record(spec.key, now=now)
        return state
