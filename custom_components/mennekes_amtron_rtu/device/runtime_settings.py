"""Validated, credential-free previews of saved versus effective service options."""

from __future__ import annotations

from typing import Any

from .service_config import ServerConfig

RUNTIME_FIELDS = (
    "scan_interval", "measurement_interval", "current_cap",
    "state_change_interval_seconds", "phase_switch_grace_seconds",
)
HOT_FIELDS = frozenset({"scan_interval", "measurement_interval"})


def runtime_values(config: ServerConfig) -> dict[str, float]:
    """Publish only these numeric settings, never a connection or credential."""
    return {key: float(getattr(config, key)) for key in RUNTIME_FIELDS}


def runtime_candidate(
    saved: dict[str, Any], changes: object, *, token: str,
) -> tuple[dict[str, Any], ServerConfig]:
    """Validate the complete candidate through the existing config boundary."""
    if not isinstance(changes, dict) or not changes or changes.keys() - set(RUNTIME_FIELDS):
        raise ValueError("invalid runtime settings")
    updated = {**saved, **changes}
    config = ServerConfig.from_dict({**updated, "token": token})
    return updated, config


def runtime_preview(active: ServerConfig, saved: ServerConfig, proposed: ServerConfig) -> dict[str, Any]:
    """Separate stored edits, hot application and changes requiring restart."""
    current, original, target = (runtime_values(config) for config in (active, saved, proposed))
    changes = [
        {"key": key, "effective": current[key], "saved": original[key], "proposed": target[key],
         "effect": "live" if key in HOT_FIELDS else "restart"}
        for key in RUNTIME_FIELDS if current[key] != target[key] or original[key] != target[key]
    ]
    restart = [key for key in RUNTIME_FIELDS if key not in HOT_FIELDS and current[key] != target[key]]
    # Do not hide unrelated pending changes made in the native Supervisor form.
    restart.extend(
        key for key in ("connection", "read_only", "listen", "port")
        if getattr(active, key) != getattr(proposed, key)
    )
    return {"active": current, "saved": original, "proposed": target,
            "changes": changes, "restart_fields": restart, "restart_required": bool(restart)}
