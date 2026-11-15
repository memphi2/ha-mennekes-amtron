"""Bounded presentation of uncertain observations; never a control input."""

from __future__ import annotations

from .charging_status import CHARGING_STATUS_OPTIONS, charging_status
from .data import WallboxData

DISPLAY_OPTIONS = (*CHARGING_STATUS_OPTIONS, "checking_status", "state_uncertain", "data_missing")
_REQUIRED = ("evse_state", "error_code", "lock_evse", "charging_release", "charging_current_ems", "authorization_status")


class ChargingDisplay:
    """Count actual polls, not HTTP requests or entity property reads."""

    def __init__(self) -> None:
        self._since: float | None = None
        self._samples = 0

    def reset(self) -> None:
        self._since = None
        self._samples = 0

    def observe(self, data: WallboxData, *, cp_supported: bool, now: float) -> str:
        fresh = WallboxData(values={key: value for key, value in data.values.items() if key not in data.held})
        status = charging_status(fresh, cp_supported=cp_supported)
        if status is not None:
            self.reset()
            return status
        required = (*_REQUIRED, "cp_state") if cp_supported else _REQUIRED
        if any(fresh.get(key) is None for key in required):
            self.reset()
            return "data_missing"
        if self._since is None:
            self._since = now
        self._samples = min(2, self._samples + 1)
        return "state_uncertain" if self._samples >= 2 and now - self._since >= 10 else "checking_status"
