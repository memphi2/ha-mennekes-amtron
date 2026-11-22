"""How Home Assistant reaches the service: over HTTP, or in its own process.

Both ways answer a request with the HTTP status and JSON object the service's
API defines, so the client interprets them in one place.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from aiohttp import ClientSession, ClientTimeout

from .device.service_api import ServiceApi
from .service_errors import ServiceConnectionError

_TIMEOUT_SECONDS = 15
# Longer than any command's own waits between its writes.
_DRAIN_SECONDS = 30
_CANCEL_SECONDS = 5
_MAX_RESPONSE_BYTES = 131072

Reply = tuple[int, dict[str, Any]]


_LOGGER = logging.getLogger(__name__)

class ServiceTransport(Protocol):
    async def call(self, method: str, path: str, payload: dict[str, Any] | None) -> Reply:
        """Return the status and JSON object; raise for a failed exchange."""


class HttpTransport:
    """The app, or a standalone service, behind its authenticated HTTP API."""

    def __init__(self, session: ClientSession, url: str, token: str) -> None:
        self._session = session
        self._url = url
        self._token = token

    async def call(self, method: str, path: str, payload: dict[str, Any] | None) -> Reply:
        async with self._session.request(
            method,
            self._url + path,
            headers={"Authorization": f"Bearer {self._token}"},
            json=payload,
            timeout=ClientTimeout(total=_TIMEOUT_SECONDS),
            allow_redirects=False,
        ) as response:
            if response.status in (401, 403):
                raise ServiceConnectionError("service_auth_failed", "service authentication failed")
            if response.status == 404 and path == "/v1/state":
                raise ServiceConnectionError("service_version_mismatch", "service API version differs")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                raw.extend(chunk)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise ServiceConnectionError("service_invalid_response", "service response too large")
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError) as err:
                raise ServiceConnectionError("service_invalid_response", "service response is not JSON") from err
            if not isinstance(data, dict):
                raise ServiceConnectionError("service_invalid_response", "service response is not an object")
            return response.status, data


def _as_json(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except TypeError as err:
        # What HTTP could not carry is an invalid message here too.
        raise ValueError("not a JSON value") from err


class EmbeddedTransport:
    """The same service running inside Home Assistant, for a direct connection.

    A request that times out or whose caller is cancelled keeps running, as it
    would behind HTTP: a command must never be abandoned between its writes.
    """

    def __init__(self, api: ServiceApi) -> None:
        self._api = api
        self._running: set[asyncio.Task[Reply]] = set()
        self._closed = False

    async def call(self, method: str, path: str, payload: dict[str, Any] | None) -> Reply:
        if self._closed:
            # A closed web server refuses the connection.
            return 503, {"error": "service_unavailable"}
        # JSON in both directions keeps the types and isolation HTTP gives.
        request = None if payload is None else _as_json(payload)
        task = asyncio.create_task(self._api.dispatch(method, path, request))
        self._running.add(task)
        task.add_done_callback(self._finished)
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            status, body = await asyncio.shield(task)
        return status, _as_json(body)

    def _finished(self, task: asyncio.Task[Reply]) -> None:
        self._running.discard(task)
        if not task.cancelled():
            # An abandoned request's failure was already answered by a timeout.
            task.exception()

    async def async_drain(self) -> None:
        """Take no new requests and let the running ones finish, before the service stops.

        A request stuck behind a hung connection is given up after a while:
        nothing may keep the bus from being handed on.
        """
        self._closed = True
        deadline = asyncio.get_running_loop().time() + _DRAIN_SECONDS
        while self._running:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                stuck = tuple(self._running)
                _LOGGER.warning("Service: %d request(s) abandoned while stopping", len(stuck))
                for task in stuck:
                    task.cancel()
                # Not for ever either: one that ignores even this is left.
                await asyncio.wait(stuck, timeout=_CANCEL_SECONDS)
                return
            # Waiting cancels none of them: a command finishes its writes.
            await asyncio.wait(tuple(self._running), timeout=left)
