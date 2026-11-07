"""Persist bounded direct-mode models on changes, outside Recorder."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol, Self

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.util.file import WriteError, write_utf8_file_atomic

from .device.charging_session import SessionHistory
from .device.const import DOMAIN
from .device.energy_goal import EnergyGoal
from .device.soc_goal import SocGoal
from .timed_goal import TimedGoal


class _GoalModel(Protocol):
    """The bounded durable state shared by independent models."""

    def snapshot(self) -> dict[str, Any]: ...

    @classmethod
    def restore(cls, raw: object) -> Self: ...


async def _finish_atomic(task: asyncio.Task[None]) -> None:
    """Keep the storage lock until the executor has really finished."""
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated cancellation must not cancel the executor's owning task:
        # the thread would continue while a newer file operation starts.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class ModelStore[Goal: _GoalModel]:
    """Atomically persist one model, isolated by store key and wallbox identity."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, identity: str, *,
        kind: str, model: type[Goal],
    ) -> None:
        self._hass = hass
        self._identity = identity
        self._model = model
        self._label = kind.replace("_", " ")
        self._key = f"{DOMAIN}.{kind}.{entry_id}"
        self.path = Path(hass.config.path(STORAGE_DIR, self._key))
        self._lock = asyncio.Lock()
        self._last: dict[str, Any] | None = None

    async def async_load(self) -> Goal:
        """Read durable state without moving corrupt files aside or caching them."""
        async with self._lock:
            payload = await self._hass.async_add_executor_job(self._validate_file)
            goal = self._validate_payload(payload) if payload is not None else self._model()
            self._last = payload
            return goal

    def _validate_file(self) -> dict[str, Any] | None:
        # HA's Store normally moves malformed JSON aside and starts empty.
        # Losing an armed energy limit silently would permit unbounded charging.
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(32769)
        except FileNotFoundError:
            return None
        if len(raw) > 32768:
            raise ValueError(f"saved {self._label} exceeds its size limit")
        try:
            envelope = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise ValueError(f"saved {self._label} is not readable") from err
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("version")) is not int
            or envelope["version"] != 1
            or type(envelope.get("minor_version", 1)) is not int
            or envelope.get("minor_version", 1) != 1
            or envelope.get("key", self._key) != self._key
        ):
            raise ValueError(f"unsupported saved {self._label} format")
        goal = self._validate_payload(envelope.get("data"))
        return {"identity": self._identity, "goal": goal.snapshot()}

    def _validate_payload(self, payload: object) -> Goal:
        if (
            not isinstance(payload, dict)
            or payload.keys() != {"identity", "goal"}
            or payload["identity"] != self._identity
        ):
            raise ValueError(f"saved {self._label} does not match this wallbox")
        return self._model.restore(payload["goal"])

    async def async_save(self, goal: Goal) -> None:
        """Save only durable changes; finish an atomic save during unload."""
        payload = {"identity": self._identity, "goal": goal.snapshot()}
        async with self._lock:
            if payload == self._last:
                return
            await _finish_atomic(asyncio.create_task(self._async_write(payload)))

    async def _async_write(self, payload: dict[str, Any]) -> None:
        await self._hass.async_add_executor_job(self._write, payload)
        self._last = payload

    def _write(self, payload: dict[str, Any]) -> None:
        # Store.async_save can defer until FINAL_WRITE during shutdown and
        # otherwise swallows write errors. A limit must be durable on return,
        # including a stop latch found while HA is stopping. Keep HA's storage
        # envelope but use its public atomic writer, off the event loop.
        envelope = {"version": 1, "minor_version": 1, "key": self._key, "data": payload}
        serialized = json.dumps(envelope, allow_nan=False, indent=2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_utf8_file_atomic(str(self.path), serialized, private=True)
        except WriteError as err:
            raise OSError(f"{self._label} could not be persisted") from err

    async def async_remove(self) -> None:
        """Remove obsolete local intent after removal or service takeover."""
        async with self._lock:
            await _finish_atomic(asyncio.create_task(self._async_remove()))

    async def _async_remove(self) -> None:
        await self._hass.async_add_executor_job(lambda: self.path.unlink(missing_ok=True))
        self._last = None


class GoalStore(ModelStore[EnergyGoal]):
    """Keep the energy baseline durable without saving each meter observation."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="energy_goal", model=EnergyGoal)


class SocGoalStore(ModelStore[SocGoal]):
    """Keep the vehicle-charge goal in its own file, independent of energy goals."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="soc_goal", model=SocGoal)


class SessionHistoryStore(ModelStore[SessionHistory]):
    """Keep one completed session, without persisting individual meter readings."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="charging_session", model=SessionHistory)


class TimedGoalStore(ModelStore[TimedGoal]):
    """Keep a single trip time durable without Recorder history."""

    def __init__(self, hass: HomeAssistant, entry_id: str, identity: str) -> None:
        super().__init__(hass, entry_id, identity, kind="timed_goal", model=TimedGoal)
