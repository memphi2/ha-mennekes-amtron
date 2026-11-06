"""Persist only accepted control intent, with atomic replacement and no history."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .energy_goal import EnergyGoal
from .service_config import number
from .soc_goal import SocGoal
from .write_state import WriteState


class IntentStore:
    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        self._last: dict[str, Any] | None = None

    def load(self, state: WriteState) -> None:
        try:
            payload = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
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
        goal = EnergyGoal.restore(payload["energy_goal"]) if "energy_goal" in payload else EnergyGoal()
        soc_goal = SocGoal.restore(payload["soc_goal"]) if "soc_goal" in payload else SocGoal()
        state.pause_requested = payload.get("pause_requested")
        state.charging_release_requested = payload.get("charging_release_requested")
        state.charging_setpoint = current
        state.energy_goal = goal
        state.soc_goal = soc_goal
        self._last = payload

    def save(self, state: WriteState) -> None:
        payload = {
            "version": 1,
            "identity": self.identity,
            "pause_requested": state.pause_requested,
            "charging_release_requested": state.charging_release_requested,
            "charging_setpoint": state.charging_setpoint,
            "energy_goal": state.energy_goal.snapshot(),
            "soc_goal": state.soc_goal.snapshot(),
        }
        if payload == self._last:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".intent-", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(payload, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._last = payload
        finally:
            temporary.unlink(missing_ok=True)
