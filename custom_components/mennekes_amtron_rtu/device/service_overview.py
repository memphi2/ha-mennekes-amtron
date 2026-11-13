"""Passive, allowlisted overview of the service, not a second charging policy."""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .charging_status import charging_status
from .command_events import CONTROL_ACTIONS, CommandEvents
from .data import WallboxData
from .operation import Operation
from .registers import CP_STATE

if TYPE_CHECKING:
    from .service_runtime import ModbusService


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _instant(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _operation(raw: object) -> dict[str, Any] | None:
    """The service's own summary, or nothing when it does not hold up."""
    if not isinstance(raw, dict):
        return None
    try:
        Operation.restore(raw)
    except ValueError:
        return None
    return raw


def overview_snapshot(
    snapshot: dict[str, Any] | None, *, ready: bool = True,
    held: frozenset[str] = frozenset(), goal_stop: bool = False,
) -> dict[str, Any]:
    """Never publish identifiers, raw errors or stale readbacks.

    The summary of who controls the charge is the service's own, passed on
    only when it validates; this page infers no plan of its own.

    Intent and goal settings remain visible during a bus outage, but they are
    not measurements. The existing status function determines observed state;
    requested changes never stand in for hardware acknowledgement.
    """
    configured = snapshot is not None
    raw = snapshot or {}
    control = _mapping(raw.get("control"))
    available = configured and ready and raw.get("available") is True
    values = {
        key: value for key, value in _mapping(raw.get("values")).items()
        if available and key not in held
    }
    layout = _mapping(raw.get("identity")).get("layout_version")
    status = charging_status(
        WallboxData(values=values),
        cp_supported=type(layout) is int and layout >= CP_STATE.min_layout,
    ) if available else None
    gate = _mapping(control.get("plug_charge"))
    read_only = control.get("read_only")
    read_only = read_only if type(read_only) is bool else None
    pending = control.get("pending_action")
    pending = pending if pending in CONTROL_ACTIONS else None
    energy = _mapping(control.get("energy_goal"))
    soc = _mapping(control.get("soc_goal"))
    if not configured:
        reason = "setup_required"
    elif not ready:
        reason = "service_starting"
    elif not available:
        reason = "wallbox_unavailable"
    elif status in (None, "initializing", "wallbox_error", "service_mode", "no_vehicle"):
        reason = status or "state_unknown"
    elif goal_stop:
        reason = "goal_stop_requested"
    elif control.get("charging_release_requested") is False:
        reason = "release_stop_requested"
    elif gate.get("enabled") is True and not (
        gate.get("authorized") is True or control.get("plug_charge_manual") is True
    ):
        reason = "plug_charge_waiting"
    elif control.get("pause_requested") is True:
        reason = "pause_requested"
    else:
        reason = status
    heartbeat = _mapping(raw.get("heartbeat"))
    healthy = heartbeat.get("healthy")
    events = CommandEvents.parse(raw.get("command_events"))
    return {
        "schema": 1,
        "configured": configured,
        "available": available,
        "state": status or "unknown",
        "reason": reason,
        "read_only": read_only,
        "heartbeat": (
            "disabled" if read_only else
            "ok" if healthy is True else "problem" if healthy is False else "unknown"
        ),
        "pending": None if pending is None else {
            "action": pending, "not_before": _instant(control.get("pending_until")),
        },
        "measurements": {
            "power_w": _number(values.get("power_total")),
            "session_kwh": _number(values.get("session_energy")),
            "current_limit_a": _number(values.get("charging_current_ems")),
        },
        "targets": {
            "energy_kwh": _number(energy.get("target_kwh")),
            "soc_percent": _number(soc.get("target_percent")),
        },
        "operation": _operation(control.get("operation")) if configured and ready else None,
        "commands": [asdict(event) for event in reversed(events[2])] if events else [],
    }


async def service_overview(service: ModbusService | None) -> dict[str, Any]:
    """Read a command-consistent RAM snapshot; no extra Modbus or disk access."""
    if service is None:
        return overview_snapshot(None)
    snapshot = await service.stable_snapshot(patient=True)
    control = service.control
    return overview_snapshot(
        snapshot, ready=control is not None, held=service.data.held,
        goal_stop=control.energy_goal_stop_requested if control is not None else False,
    )
