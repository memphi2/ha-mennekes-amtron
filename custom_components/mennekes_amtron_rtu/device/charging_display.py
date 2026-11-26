"""Bounded presentation of uncertain observations; never a control input."""

from __future__ import annotations

from .charging_status import CHARGING_STATUS_OPTIONS, charging_status
from .data import WallboxData
from .enums import CpState, EvseState

DISPLAY_OPTIONS = (
    *CHARGING_STATUS_OPTIONS, "checking_status", "phase_switching", "state_uncertain", "data_missing",
)
_REQUIRED = ("evse_state", "error_code", "lock_evse", "charging_release", "charging_current_ems", "authorization_status")
# After a phase change the wallbox withholds its charging signal for about two
# minutes (CP b1 or c1) while it still reports charging. Within this time
# after a changed phase register that is the switch, not a contradiction.
PHASE_SWITCH_SECONDS = 300.0
_PHASE_KEYS = ("requested_phases", "switched_phases")
_NO_SIGNAL = (CpState.B1, CpState.C1)


class ChargingDisplay:
    """Count actual polls, not HTTP requests or entity property reads."""

    def __init__(self) -> None:
        self._since: float | None = None
        self._samples = 0
        # The last read phase registers, and when one of them last changed.
        self._phases: dict[str, int] = {}
        self._phase_changed_at: float | None = None

    def reset(self) -> None:
        self._since = None
        self._samples = 0

    def observe(self, data: WallboxData, *, cp_supported: bool, now: float) -> str:
        fresh = WallboxData(values={key: value for key, value in data.values.items() if key not in data.held})
        self._note_phases(fresh, now)
        status = charging_status(fresh, cp_supported=cp_supported)
        if status is not None:
            self.reset()
            return status
        required = (*_REQUIRED, "cp_state") if cp_supported else _REQUIRED
        if any(fresh.get(key) is None for key in required):
            self.reset()
            return "data_missing"
        if self._switching_phases(fresh, cp_supported=cp_supported, now=now):
            self.reset()
            return "phase_switching"
        if self._since is None:
            self._since = now
        self._samples = min(2, self._samples + 1)
        return "state_uncertain" if self._samples >= 2 and now - self._since >= 10 else "checking_status"

    def _note_phases(self, fresh: WallboxData, now: float) -> None:
        for key in _PHASE_KEYS:
            value = fresh.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            if key in self._phases and self._phases[key] != value:
                self._phase_changed_at = now
            self._phases[key] = value

    def _switching_phases(self, fresh: WallboxData, *, cp_supported: bool, now: float) -> bool:
        """Charging, and only the missing charging signal disagrees."""
        if not (
            cp_supported and self._phase_changed_at is not None
            and now - self._phase_changed_at <= PHASE_SWITCH_SECONDS
            and fresh.get("evse_state") == EvseState.CHARGING
            and fresh.get("cp_state") in _NO_SIGNAL
        ):
            return False
        signalled = WallboxData(values={**fresh.values, "cp_state": CpState.C2})
        return charging_status(signalled, cp_supported=True) == "charging"
