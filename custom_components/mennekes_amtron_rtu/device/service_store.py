"""Persist accepted control intent and one completed session atomically."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .charging_session import SessionHistory
from .energy_goal import EnergyGoal
from .guest_permission import GuestPermission
from .persistence import StorageCommitUncertainError, replace_file
from .plug_charge import PlugChargeState
from .service_config import number
from .soc_goal import SocGoal
from .unplug_release import UnplugReleaseState
from .write_state import WriteState

_LOGGER = logging.getLogger(__name__)


class IntentStore:
    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: dict[str, Any] | None = None

    def load(self, state: WriteState) -> bool:
        """Apply the saved intent; False when the file was rejected.

        A missing file is a first start. An incompatible or damaged file is
        rejected without applying it. The service must preserve that file and
        block charging commands until the user acknowledges a safe reset.
        """
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return True
        try:
            self._apply(json.loads(raw), state)
        except (ValueError, TypeError, ArithmeticError, RecursionError):
            return False
        return True

    def _apply(self, payload: Any, state: WriteState) -> None:
        if (
            not isinstance(payload, dict)
            or payload.keys() != {"version", "identity", "pause_requested", "unplug_release",
                                  "charging_release_requested", "charging_setpoint",
                                  "energy_goal", "soc_goal", "plug_charge", "charging_session_history", "guest_permission"}
            or payload.get("version") != 2
            or payload.get("identity") != self.identity
        ):
            raise ValueError("saved intent does not match this wallbox or state format")
        for key in ("pause_requested", "charging_release_requested"):
            value = payload.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError("invalid saved control intent")
        current = payload.get("charging_setpoint")
        if current is not None:
            current = number(current, 0, 32)
            if 0 < current < 6:
                raise ValueError("invalid saved resume current")
        guest = None if payload["guest_permission"] is None else GuestPermission.restore(payload["guest_permission"])
        goal = EnergyGoal.restore(payload["energy_goal"])
        soc_goal = SocGoal.restore(payload["soc_goal"])
        plug_charge = PlugChargeState.restore(payload["plug_charge"])
        unplug_release = UnplugReleaseState.restore(payload["unplug_release"])
        history = SessionHistory()
        try:
            history = SessionHistory.restore(payload["charging_session_history"])
        except ValueError:
            # Optional measurements must not block valid control intent
            # or heartbeat startup. Do not expose stored data in the log.
            _LOGGER.warning("Charging session: ignoring invalid saved history")
        state.pause_requested = payload.get("pause_requested")
        state.charging_release_requested = payload.get("charging_release_requested")
        state.charging_setpoint = current
        state.guest_permission = guest
        state.energy_goal = goal
        state.soc_goal = soc_goal
        state.session_history = history
        state.plug_charge = plug_charge
        state.unplug_release = unplug_release
        self._last = payload

    def save(self, state: WriteState) -> None:
        payload = {
            "version": 2,
            "identity": self.identity,
            "pause_requested": state.pause_requested,
            "charging_release_requested": state.charging_release_requested,
            "charging_setpoint": state.charging_setpoint,
            "guest_permission": state.guest_permission.snapshot() if state.guest_permission is not None else None,
            "energy_goal": state.energy_goal.snapshot(),
            "soc_goal": state.soc_goal.snapshot(),
            "charging_session_history": state.session_history.snapshot(),
            "plug_charge": state.plug_charge.durable(),
            "unplug_release": state.unplug_release.snapshot(),
        }
        if payload == self._last:
            return
        try:
            replace_file(self.path, json.dumps(payload, allow_nan=False).encode(), prefix=".intent-")
        except StorageCommitUncertainError:
            # The target file already names the new snapshot: the goal
            # controllers must not take it for an uncommitted save, and the
            # older cached payload must not skip the write that restores it.
            self._last = None
            raise
        self._last = payload
