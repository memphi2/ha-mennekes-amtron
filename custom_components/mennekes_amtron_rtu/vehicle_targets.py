"""Remember each vehicle's charge target across vehicle changes."""

from __future__ import annotations

import logging
import math
from typing import Any, Self

from homeassistant.core import HomeAssistant

from .goal_store import ModelStore

_LOGGER = logging.getLogger(__name__)
# More than the number of profiles a household configures; oldest drop first.
MAX_TARGETS = 16


class VehicleTargets:
    """Charge targets by vehicle source; zero records an explicit disable."""

    def __init__(self, targets: dict[str, float] | None = None) -> None:
        self.targets = dict(targets or {})

    def snapshot(self) -> dict[str, Any]:
        return {"targets": dict(self.targets)}

    @classmethod
    def restore(cls, raw: object) -> Self:
        if not isinstance(raw, dict) or raw.keys() != {"targets"} or not isinstance(raw["targets"], dict):
            raise ValueError("invalid saved vehicle targets")
        targets = raw["targets"]
        if len(targets) > MAX_TARGETS or not all(
            isinstance(source, str) and 0 < len(source) <= 512
            and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100
            for source, value in targets.items()
        ):
            raise ValueError("invalid saved vehicle targets")
        return cls({source: float(value) for source, value in targets.items()})

    def remember(self, source: str, target: float) -> bool:
        if self.targets.get(source) == target:
            return False
        self.targets.pop(source, None)
        self.targets[source] = target
        while len(self.targets) > MAX_TARGETS:
            del self.targets[next(iter(self.targets))]
        return True


class VehicleTargetStore(ModelStore[VehicleTargets]):
    """Keep remembered targets in their own file, outside the active goal."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="vehicle_targets", model=VehicleTargets)


class VehicleTargetMemory:
    """Best-effort memory; a storage problem never blocks charging control."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        self._store = VehicleTargetStore(hass, entry_id, identity)
        self._targets = VehicleTargets()

    async def async_load(self) -> None:
        try:
            self._targets = await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Ignoring saved vehicle charge targets: %s", err)

    def recall(self, source: str) -> float | None:
        return self._targets.targets.get(source)

    def remember(self, source: str, target: float) -> bool:
        """Record a target; the caller saves when this returns True."""
        return self._targets.remember(source, target)

    async def async_save(self) -> None:
        try:
            await self._store.async_save(VehicleTargets(self._targets.targets))
        except OSError as err:
            _LOGGER.warning("Vehicle charge target not saved: %s", err)
