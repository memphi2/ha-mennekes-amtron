"""Explain the wallbox's observed state without guessing why a vehicle stopped."""

from __future__ import annotations

import math

from .const import CHARGING_CURRENT_MINIMUM
from .data import WallboxData
from .decode import RegisterValue
from .enums import AuthorizationStatus, CpState, EvseState
from .vehicle import vehicle_connected

CHARGING_STATUS_OPTIONS = (
    "initializing", "wallbox_error", "service_mode", "no_vehicle", "charging",
    "locked", "release_disabled", "paused", "authorization_required",
    "preparing", "waiting_for_vehicle",
)


def charging_status(data: WallboxData, *, cp_supported: bool) -> str | None:
    """Return one verified state or blocking setting, with stable precedence.

    Fault/service/startup states take precedence, then an absent vehicle.
    Connected vehicles report charging only without contradictory control
    readback. Otherwise lock, release, pause and authorization are checked in
    that order. A pending command never substitutes for actual readback.
    """
    state = _integer(data.get("evse_state"))
    if state is None or state not in EvseState:
        return None
    error = _integer(data.get("error_code"))
    if state == EvseState.ERROR or (error is not None and error > 0):
        return "wallbox_error"
    if state == EvseState.NOT_INITIALIZED:
        return "initializing"
    if state == EvseState.SERVICE_MODE:
        return "service_mode"
    cp = data.get("cp_state")
    present = vehicle_connected(state, cp, cp_supported=cp_supported)
    if present is False:
        return "no_vehicle"
    if present is None:
        return None

    locked = _flag(data.get("lock_evse"))
    released = _flag(data.get("charging_release"))
    paused = _paused(data.get("charging_current_ems"))
    authorization = _integer(data.get("authorization_status"))
    if state == EvseState.CHARGING:
        if (
            locked is True or released is False or paused is True
            or authorization == AuthorizationStatus.NOT_AUTHORIZED
            or (
                cp_supported and cp is not None
                and cp not in (CpState.C2, CpState.D2)
            )
        ):
            return None
        return "charging"

    if locked is True:
        return "locked"
    if released is False:
        return "release_disabled"
    if paused is True:
        return "paused"
    if authorization == AuthorizationStatus.NOT_AUTHORIZED:
        return "authorization_required"
    # Do not blame the car while a known wallbox-side prerequisite is unreadable.
    if (
        locked is None or released is None or paused is None
        or authorization not in (AuthorizationStatus.AUTHORIZED, AuthorizationStatus.NOT_USED)
        or error != 0
        or (cp_supported and cp in (CpState.INIT, CpState.E, CpState.F))
    ):
        return None
    if state == EvseState.READY_TO_CHARGE:
        return "waiting_for_vehicle"
    return "preparing"


def _integer(value: RegisterValue) -> int | None:
    """Decoded integer registers never use booleans or negative sentinels."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _flag(value: RegisterValue) -> bool | None:
    integer = _integer(value)
    return bool(integer) if integer in (0, 1) else None


def _paused(value: RegisterValue) -> bool | None:
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < 0
    ):
        return None
    # Zero explicitly removes the EMS limit; only the subminimum range pauses.
    return 0 < value < CHARGING_CURRENT_MINIMUM
