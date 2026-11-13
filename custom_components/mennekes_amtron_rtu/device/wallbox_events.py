"""Changes the wallbox itself reports between two polls, for automations.

Only a change between two known readings creates an event. The first reading
after a start, a reconnect or a failed poll sets the baseline, so nothing is
reported at startup and nothing is replayed afterwards. Measurements such as
current, power and temperature never create events; neither do our own
commands, which the command-result feed reports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from .data import WallboxData
from .decode import RegisterValue, as_integer, as_number
from .enums import (
    AuthorizationStatus,
    CableLockStatus,
    CpState,
    DowngradeStatus,
    EvseState,
    SwitchedPhases,
    enum_option,
    error_category,
)
from .registers import (
    AUTHORIZATION_STATUS,
    CABLE_LOCK_STATUS,
    CP_STATE,
    DOWNGRADE_CURRENT,
    DOWNGRADE_STATUS,
    ERROR_CODE,
    EVSE_STATE,
    SESSION_DURATION,
    SESSION_ENERGY,
    SWITCHED_PHASES,
)
from .vehicle import vehicle_connected

EVENT_TYPES = (
    "vehicle_plugged_in", "vehicle_unplugged",
    "charging_started", "charging_stopped",
    "error_occurred", "error_cleared",
    "authorized", "authorization_rejected",
    "downgrade_started", "downgrade_ended",
    "cable_locked", "cable_unlocked",
    "phases_switched",
)
ATTRIBUTES = (
    "cp_state", "evse_state", "session_energy", "session_duration",
    "error_code", "error_category", "authorization", "downgrade_current",
    "cable_lock", "phases",
)
_SESSION_KEYS = (SESSION_ENERGY.key, SESSION_DURATION.key)


@dataclass(frozen=True, slots=True)
class WallboxEvent:
    """One reported change with the stable attribute values of its moment."""

    type: str
    attributes: Mapping[str, Any] = field(default_factory=dict)


def _option(enum: type[IntEnum], value: RegisterValue) -> str | None:
    code = as_integer(value)
    return enum_option(enum(code)) if code is not None and code in enum else None


class WallboxChanges:
    """Compare consecutive snapshots and name what the wallbox changed."""

    def __init__(self, *, cp_supported: bool) -> None:
        self._cp_supported = cp_supported
        self._known: dict[str, object] = {}
        # The last session counters seen with a car, for the unplug and stop
        # events: the wallbox may already have reset them in the poll that
        # shows the car gone.
        self._session: dict[str, float] = {}
        self._error: dict[str, object] = {}

    def observe(self, data: WallboxData | None) -> list[WallboxEvent]:
        """Return the changes since the previous known reading."""
        if data is None:
            self._known.clear()
            return []
        cp = _option(CpState, data.get(CP_STATE.key))
        evse = as_integer(data.get(EVSE_STATE.key))
        connected = vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key), cp_supported=self._cp_supported,
        )
        charging = evse == EvseState.CHARGING if evse in EvseState else None
        if connected or charging:
            self._session = {
                key: number for key in _SESSION_KEYS
                if (number := as_number(data.get(key))) is not None
            }
        code = as_integer(data.get(ERROR_CODE.key))
        if code:
            self._error = {"error_code": code, "error_category": enum_option(error_category(code))}
        downgrade = as_integer(data.get(DOWNGRADE_STATUS.key))
        lock = as_integer(data.get(CABLE_LOCK_STATUS.key))
        authorization = _option(AuthorizationStatus, data.get(AUTHORIZATION_STATUS.key))
        phases = _option(SwitchedPhases, data.get(SWITCHED_PHASES.key))
        # A charge that ends with the unplug is reported before the unplug.
        return [
            *self._flag(
                "charging", charging,
                lambda: WallboxEvent("charging_started", {"evse_state": _option(EvseState, evse)}),
                lambda: WallboxEvent(
                    "charging_stopped", {"evse_state": _option(EvseState, evse), **self._session},
                ),
            ),
            *self._flag(
                "connected", connected,
                lambda: WallboxEvent("vehicle_plugged_in", {"cp_state": cp}),
                lambda: WallboxEvent("vehicle_unplugged", {"cp_state": cp, **self._session}),
            ),
            *self._flag(
                "error", None if code is None else code != 0,
                lambda: WallboxEvent("error_occurred", dict(self._error)),
                lambda: WallboxEvent("error_cleared", dict(self._error)),
            ),
            *self._option_change("authorization", authorization, {
                enum_option(AuthorizationStatus.AUTHORIZED): "authorized",
                enum_option(AuthorizationStatus.NOT_AUTHORIZED): "authorization_rejected",
            }, {"authorization": authorization}),
            *self._flag(
                "downgrade",
                downgrade == DowngradeStatus.DOWNGRADED if downgrade in DowngradeStatus else None,
                lambda: WallboxEvent(
                    "downgrade_started",
                    {"downgrade_current": as_number(data.get(DOWNGRADE_CURRENT.key))},
                ),
                lambda: WallboxEvent("downgrade_ended"),
            ),
            *self._flag(
                "cable_lock",
                True if lock == CableLockStatus.LOCKED
                else False if lock == CableLockStatus.UNLOCKED else None,
                lambda: WallboxEvent("cable_locked", {"cable_lock": _option(CableLockStatus, lock)}),
                lambda: WallboxEvent("cable_unlocked", {"cable_lock": _option(CableLockStatus, lock)}),
            ),
            *self._option_change(
                "phases", phases, dict.fromkeys(
                    (enum_option(member) for member in SwitchedPhases), "phases_switched",
                ), {"phases": phases},
            ),
        ]

    def _flag(
        self, aspect: str, value: bool | None,
        on: Callable[[], WallboxEvent], off: Callable[[], WallboxEvent],
    ) -> list[WallboxEvent]:
        """A flag that is unknown for a while keeps its last known baseline."""
        if value is None:
            return []
        previous = self._known.get(aspect)
        self._known[aspect] = value
        if previous is None or previous == value:
            return []
        return [on() if value else off()]

    def _option_change(
        self, aspect: str, value: str | None,
        events: Mapping[str, str], attributes: Mapping[str, Any],
    ) -> list[WallboxEvent]:
        """A state that changed to one of the named options."""
        if value is None:
            return []
        previous = self._known.get(aspect)
        self._known[aspect] = value
        if previous is None or previous == value or value not in events:
            return []
        return [WallboxEvent(events[value], dict(attributes))]
