"""How Home Assistant reaches the service: over HTTP, or in its own process.

Both ways answer a request with the HTTP status and JSON object the service's
API defines, so the client interprets them in one place.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from aiohttp import ClientSession, ClientTimeout

from .device.service_api import ServiceApi, ServiceBusyError
from .remote_errors import ServiceConnectionError

_TIMEOUT_SECONDS = 15
_MAX_RESPONSE_BYTES = 131072

Reply = tuple[int, dict[str, Any]]


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
            if response.status == 404 and path == "/v2/state":
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


class EmbeddedTransport:
    """The same service running inside Home Assistant, for a direct connection.

    A request that times out or whose caller is cancelled keeps running, as it
    would behind HTTP: a command must never be abandoned between its writes.
    """

    def __init__(self, api: ServiceApi) -> None:
        self._api = api
        self._running: set[asyncio.Task[Reply]] = set()

    async def call(self, method: str, path: str, payload: dict[str, Any] | None) -> Reply:
        # JSON in both directions keeps the types and isolation HTTP gives.
        request = None if payload is None else json.loads(json.dumps(payload))
        task = asyncio.create_task(self._dispatch(method, path, request))
        self._running.add(task)
        task.add_done_callback(self._finished)
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            status, body = await asyncio.shield(task)
        return status, json.loads(json.dumps(body))

    async def _dispatch(self, method: str, path: str, payload: dict[str, Any] | None) -> Reply:
        try:
            return await self._api.dispatch(method, path, payload)
        except ServiceBusyError:
            return 503, {"error": "service_unavailable"}

    def _finished(self, task: asyncio.Task[Reply]) -> None:
        self._running.discard(task)
        if not task.cancelled():
            # An abandoned request's failure was already answered by a timeout.
            task.exception()

    async def async_drain(self) -> None:
        """Wait for requests their callers gave up on, before the service stops."""
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)
