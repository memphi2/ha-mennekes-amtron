"""What is known about the connection right now, for every rule that asks before it acts."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .session_mark import SessionMark

if TYPE_CHECKING:
    from .service_runtime import ModbusService

_STAMP_TOLERANCE_SECONDS = 24 * 3600.0
_STATUS_KEYS = ("error_code", "lock_evse", "charging_release")


@dataclass(frozen=True, slots=True)
class ConnectionSample:
    """A passive snapshot. ``scope`` is a per-connection opaque digest, not a VIN."""

    now: datetime
    available: bool
    present: bool | None
    scope: str | None
    total_kwh: float | None
    current_a: float | None
    current_cap: float
    pending: bool = False
    # False while the error, lock or release value is held from a lost reply.
    status_known: bool = True
    # The wallbox's record of the current plug-in, None until read in full.
    session: SessionMark | None = None


def connection_sample(service: ModbusService) -> ConnectionSample:
    """Read the plug cycle, the meter and the applied current as they are now."""
    control = service.control
    cycle = service.client.write_state.plug_cycle
    available = service.is_available() and not service.stopping
    present = cycle.present if available else None
    # Policy checks run before every write; read the sample, not a snapshot.
    sample = service.energy_sample
    now = datetime.now(UTC)
    total = sample.get("value") if isinstance(sample, dict) else None
    stamp = sample.get("observed_at") if isinstance(sample, dict) else None
    # Judged on the monotonic clock: a wall-clock step must not pause a
    # charge for a meter reading that is not old at all. The stamp itself
    # only has to be a plausible time.
    age = time.monotonic() - service.energy_sample_at if service.energy_sample_at is not None else None
    fresh = (
        available and isinstance(total, (int, float)) and not isinstance(total, bool)
        and math.isfinite(total) and total >= 0
        and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
        and math.isfinite(stamp) and abs(now.timestamp() - stamp) <= _STAMP_TOLERANCE_SECONDS
        and age is not None and 0 <= age <= max(90, service.config.scan_interval * 3)
    )
    scope = cycle.scope if control is not None else None
    current = service.data.fresh("charging_current_ems")
    valid_current = (
        available and isinstance(current, (int, float)) and not isinstance(current, bool)
        and math.isfinite(current) and current >= 0
        and service.data.current_write_generation == service.client.write_state.current_write_generation
    )
    # One lost reply holds these values: no stop can be told from a gap.
    status_known = not any(key in service.data.held for key in _STATUS_KEYS)
    current_value = float(current) if valid_current and isinstance(current, (int, float)) else None
    total_value = float(total) if fresh and isinstance(total, (int, float)) else None
    return ConnectionSample(
        now, available, present, scope, total_value, current_value,
        control.max_charging_current if control is not None else 6,
        control is not None and control.pending_action is not None,
        session=cycle.mark if scope is not None and available else None, status_known=status_known,
    )
