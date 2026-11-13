"""One evaluation of a departure plan, shared by Home Assistant and the service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .charging_plan import ChargePlan

# A plan current that moves by less than this is not written again.
MIN_CHANGE_A = 1.0
# The plan writes at most this often, so manual and PV commands keep their
# say in between.
MIN_WRITE_INTERVAL = 300.0
# A start closer than this charges at once instead of pausing for it.
PAUSE_MARGIN = timedelta(seconds=360)
# Statuses of a computed plan; a pending command keeps its start time.
PLANNED = frozenset({
    "queued", "write_pending", "paused_until_start", "charging",
    "finishing", "unreachable", "late",
})


@dataclass(frozen=True, slots=True)
class PlanStep:
    """What the plan applies now: a current, or a pause until its start."""

    status: str
    current_a: float | None
    planned_start: datetime

    @property
    def pauses(self) -> bool:
        return self.status == "paused_until_start"


def plan_step(plan: ChargePlan, planned_start: datetime | None, now: datetime) -> PlanStep:
    """Charge now or pause until the plan's late start.

    A started charge keeps going: a faster charge than estimated moves the
    computed start later, and pausing for it would only cost relay switching.
    The minimum current at the end absorbs the lead instead.
    """
    if planned_start is not None and planned_start <= now:
        return PlanStep(plan.status, plan.current_a, planned_start)
    if plan.start_at is not None and plan.start_at - now > PAUSE_MARGIN:
        return PlanStep("paused_until_start", None, plan.start_at)
    # The plan charges now; keep when it began.
    return PlanStep(plan.status, plan.current_a, now)


def same_command(previous: float | None, current: float | None) -> bool:
    """A queued plan command still fits: the same pause, or within 1 A."""
    return previous == current or (
        previous is not None and current is not None
        and abs(previous - current) < MIN_CHANGE_A
    )


def needs_current(current_a: float, *, paused: bool, applied: object) -> bool:
    """The wallbox's current is far enough from the plan's to write it."""
    return paused or not isinstance(applied, (int, float)) or (
        applied == 0 or abs(float(applied) - current_a) >= MIN_CHANGE_A
    )


def keeps_start(
    status: str, planned_start: datetime | None, now: datetime, *, keep_start: bool = False,
) -> bool:
    """A planned status keeps the start; a gap in the inputs only a past one."""
    started = planned_start is not None and planned_start <= now
    return status in PLANNED or (keep_start and started)
