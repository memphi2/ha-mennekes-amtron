"""Repair issue identifiers and their trigger conditions.

The conditions are plain functions over a snapshot so they can be tested
without Home Assistant, and so the issue text and the condition that raises
it stay in one place.
"""

from __future__ import annotations

from typing import Final

from .const import ERROR_CODE_EMS_UNAVAILABLE
from .data import DeviceIdentity, WallboxData, WriteDiagnostics
from .registers import VALIDATED_LAYOUT, layout_label

ISSUE_EMS_HEARTBEAT_LOST: Final = "ems_heartbeat_lost"
ISSUE_EMS_FALLBACK_NOT_CONFIGURED: Final = "ems_fallback_not_configured"
ISSUE_UNSUPPORTED_LAYOUT: Final = "unsupported_modbus_layout"
ISSUE_WRITE_REJECTED: Final = "write_rejected"

FIXABLE_ISSUES: Final = frozenset({ISSUE_EMS_HEARTBEAT_LOST})


def heartbeat_lost(data: WallboxData) -> bool:
    """Return true while the wallbox reports the missing-heartbeat error."""

    return data.get("error_code") == ERROR_CODE_EMS_UNAVAILABLE


def fallback_not_configured(data: WallboxData) -> bool:
    """Return true when no energy-manager fallback behaviour is configured.

    With 0x030E at 0 the wallbox keeps charging with the last values it
    received, so a Home Assistant outage leaves no defined fallback. The
    value can only be changed with the MENNEKES configuration tool.
    """

    return data.get("ems_fallback_current") == 0


def unsupported_layout(identity: DeviceIdentity) -> bool:
    """Return true for a register layout older than the validated one."""

    return identity.layout_version < VALIDATED_LAYOUT


def write_rejected(diagnostics: WriteDiagnostics) -> bool:
    """Return true once the wallbox has refused a write."""

    return diagnostics.writes_rejected > 0


def layout_placeholders(identity: DeviceIdentity) -> dict[str, str]:
    """Return the translation placeholders of the layout issue."""

    return {
        "layout": layout_label(identity.layout_version),
        "validated_layout": layout_label(VALIDATED_LAYOUT),
    }
