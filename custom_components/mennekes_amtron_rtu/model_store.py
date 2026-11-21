"""Persist what Home Assistant's side remembers per wallbox, outside Recorder.

Home Assistant's own ``Store`` is not used on purpose: it defers a save, does
not report a failed one and moves an unreadable file aside. The charge-loss
ledger and the parked trips need to know that a save is durable when it
returns, or that it is not. The files keep the Store's envelope, name and
directory, and are written with Home Assistant's atomic writer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Protocol, Self

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.util.file import WriteError, write_utf8_file_atomic

from .device.const import DOMAIN
from .device.persistence import StorageCommitUncertainError, StorageStateUnknownError


class _Model(Protocol):
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


class PendingSaves:
    """Saves started from a callback, finished before their owner unloads.

    A save that outlived its owner could write a file again after the
    entry and its files were removed.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def start(self, hass: HomeAssistant, save: Coroutine[Any, Any, Any], name: str) -> None:
        task = hass.async_create_task(save, name, eager_start=False)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_wait(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


class ModelStore[Goal: _Model]:
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
        self._write_unconfirmed = False

    async def async_load(self) -> Goal:
        """Read durable state without moving corrupt files aside or caching them."""
        async with self._lock:
            payload = await self._hass.async_add_executor_job(self._validate_file)
            goal = self._validate_payload(payload) if payload is not None else self._model()
            self._last = None if self._write_unconfirmed else payload
            return goal

    def _validate_file(self) -> dict[str, Any] | None:
        # HA's Store moves malformed JSON aside and starts empty; here the
        # caller learns that its saved state is gone.
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
        try:
            await self._hass.async_add_executor_job(self._write, payload)
        except OSError as err:
            # A rename can succeed before the directory sync fails. Invalidate
            # even the previous cache: the file may have changed, including
            # when it cannot be read back. This task stays shielded throughout
            # classification, with the store lock held.
            self._last = None
            self._write_unconfirmed = True
            try:
                visible = await self._hass.async_add_executor_job(self._validate_file)
            except (OSError, ValueError) as read_error:
                raise StorageStateUnknownError(
                    f"{self._label} could not be verified after a failed save"
                ) from read_error
            if visible == payload:
                raise StorageCommitUncertainError(
                    f"{self._label} replaced but its durability is uncertain"
                ) from err
            raise
        self._last = payload
        self._write_unconfirmed = False

    def _write(self, payload: dict[str, Any]) -> None:
        # Store.async_save can defer until FINAL_WRITE during shutdown and
        # otherwise swallows write errors. Keep HA's storage envelope but use
        # its public atomic writer, off the event loop.
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
        self._write_unconfirmed = False
