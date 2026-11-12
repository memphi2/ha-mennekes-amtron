"""Bounded, boot-scoped reservations for uncertain percentage-goal requests."""

from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .decode import is_hex32
from .persistence import StorageCommitUncertainError, StorageStateUnknownError

if TYPE_CHECKING:
    from .service_runtime import ModbusService

_LIMIT = 64
_LIFETIME = 300.0


@dataclass(slots=True)
class _Reservation:
    expires: float
    task: asyncio.Task[tuple[str, str | None]] | None = None
    cancelled: bool = False


class SocTransactions:
    """Prevent a late request from executing after an unstarted reservation is fenced."""

    def __init__(self, service: ModbusService) -> None:
        self._service = service
        self._reservations: dict[str, _Reservation] = {}
        self._closed = False

    def reserve(self) -> str:
        if self._closed:
            raise ValueError("service is stopping")
        now = time.monotonic()
        self._reservations = {
            key: item for key, item in self._reservations.items()
            if item.expires > now or (item.task is not None and not item.task.done())
        }
        if len(self._reservations) >= _LIMIT:
            raise ValueError("too many pending percentage-goal reservations")
        token = secrets.token_hex(16)
        self._reservations[token] = _Reservation(now + _LIFETIME)
        return token

    @staticmethod
    def _token(value: object) -> str:
        if not is_hex32(value):
            raise ValueError("invalid percentage-goal reservation")
        return str(value)

    async def _response(self, outcome: str, revision: str | None = None) -> dict[str, Any]:
        return {
            "outcome": outcome, "revision": revision,
            "snapshot": await self._service.stable_snapshot(),
        }

    async def commit(
        self, payload: dict[str, Any], *, gate: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        if set(payload) != {"token", "expected_serial_number", "value", "source", "soc", "maximum"}:
            raise ValueError("invalid percentage-goal request")
        token = self._token(payload["token"])
        item = self._reservations.get(token)
        if item is None or (item.task is None and item.expires <= time.monotonic()):
            return await self._response("unknown")
        if item.cancelled:
            return await self._response("cancelled")
        if item.task is None:
            command = {key: value for key, value in payload.items() if key != "token"}
            command["command"] = "soc_goal"
            item.task = asyncio.create_task(self._execute(command, gate), name="amtron_soc_transaction")
        outcome, revision = await asyncio.shield(item.task)
        return await self._response(outcome, revision)

    async def _execute(
        self, payload: dict[str, Any], gate: asyncio.Semaphore | None,
    ) -> tuple[str, str | None]:
        checked: tuple[str, str, BaseException | None] | None = None

        def record(before: str, after: str, error: BaseException | None) -> None:
            nonlocal checked
            checked = (before, after, error)

        try:
            # The task, not its HTTP handler, retains admission after a lost
            # response; cancellation must not free a slot while work continues.
            async with gate if gate is not None else nullcontext():
                result = await self._service.command(payload, _soc_outcome=record)
        except Exception:  # noqa: BLE001 - any failure needs a bounded outcome
            if checked is None:
                # Command admission failed before the mutation section.
                return "rejected", None
            before, after, error = checked
            if error is None and before != after:
                # The target was saved; only the later refresh failed.
                return "applied", after
            if before == after and error is not None and not isinstance(
                error, (StorageCommitUncertainError, StorageStateUnknownError),
            ):
                return "rejected", None
            # An uncertain storage result or changed candidate is not a rejection.
            return "unknown", None
        return "applied", result["control"]["soc_goal_revision"]

    async def resolve(self, token: object) -> dict[str, Any]:
        item = self._reservations.get(self._token(token))
        if item is None:
            return await self._response("unknown")
        if item.task is None:
            item.cancelled = True
            return await self._response("cancelled")
        outcome, revision = await asyncio.shield(item.task)
        return await self._response(outcome, revision)

    async def close(self) -> None:
        self._closed = True
        tasks = [item.task for item in self._reservations.values() if item.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
