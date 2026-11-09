"""Remember the phases each vehicle charged with, for planning its next start."""

from __future__ import annotations

import logging
from typing import Any, Self

from homeassistant.core import HomeAssistant

from .goal_store import ModelStore

_LOGGER = logging.getLogger(__name__)
# More than the number of profiles a household configures; oldest drop first.
MAX_VEHICLES = 16


class VehiclePhases:
    """The detected phase count of each profile's last charge."""

    def __init__(self, phases: dict[str, int] | None = None) -> None:
        self.phases = dict(phases or {})

    def snapshot(self) -> dict[str, Any]:
        return {"phases": dict(self.phases)}

    @classmethod
    def restore(cls, raw: object) -> Self:
        if not isinstance(raw, dict) or raw.keys() != {"phases"} or not isinstance(raw["phases"], dict):
            raise ValueError("invalid saved vehicle phases")
        phases = raw["phases"]
        if len(phases) > MAX_VEHICLES or not all(
            isinstance(key, str) and 0 < len(key) <= 64
            and type(value) is int and value in (1, 2, 3)
            for key, value in phases.items()
        ):
            raise ValueError("invalid saved vehicle phases")
        return cls(phases)


class VehiclePhaseStore(ModelStore[VehiclePhases]):
    """Keep the phases in their own file; losing it only plans with three."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="vehicle_phases", model=VehiclePhases)


class VehiclePhaseMemory:
    """Best-effort memory; a storage problem only forgets a car's phases."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        self._store = VehiclePhaseStore(hass, entry_id, identity)

    async def _async_load(self) -> VehiclePhases:
        try:
            return await self._store.async_load()
        except (OSError, ValueError) as err:
            _LOGGER.warning("Ignoring saved vehicle phases: %s", err)
            return VehiclePhases()

    async def async_phases(self, profile_id: str) -> int | None:
        return (await self._async_load()).phases.get(profile_id)

    async def async_remember(self, profile_id: str, phases: int) -> None:
        saved = await self._async_load()
        if saved.phases.get(profile_id) == phases:
            return
        saved.phases.pop(profile_id, None)
        saved.phases[profile_id] = phases
        while len(saved.phases) > MAX_VEHICLES:
            del saved.phases[next(iter(saved.phases))]
        try:
            await self._store.async_save(saved)
        except OSError as err:
            _LOGGER.warning("Vehicle phases not remembered: %s", err)
