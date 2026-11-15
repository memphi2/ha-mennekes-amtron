"""The service's operations, independent of how a caller reaches them.

The HTTP API and a caller in the same process both dispatch here, so a command
has one behavior and one error mapping whichever way it arrives.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .client_errors import AmtronBusError
from .errors import DeviceError, DeviceValidationError
from .service_runtime import ModbusService

Reply = tuple[int, dict[str, Any]]
_UNAVAILABLE: Reply = (503, {"error": "service_unavailable"})


class _NotFoundError(Exception):
    """The service has no such operation."""


class ServiceBusyError(Exception):
    """Too many callers already wait for the bus."""


class ServiceApi:
    """Versioned operations: commands and passive reads, no register writes."""

    def __init__(self, service: ModbusService) -> None:
        self._service = service
        self._bus_requests = asyncio.Semaphore(8)

    @property
    def service(self) -> ModbusService:
        return self._service

    async def dispatch(self, method: str, path: str, payload: object = None) -> Reply:
        """Answer one request with a status and a JSON object, as HTTP would.

        ServiceBusyError propagates: only the caller knows how to report it.
        """
        try:
            return 200, await self._route(method, path, payload)
        except _NotFoundError:
            return 404, {"error": "not_found"}
        except DeviceError as err:
            return 409, {
                "error": err.translation_key,
                "placeholders": err.translation_placeholders,
                "validation": isinstance(err, DeviceValidationError),
            }
        except (ValueError, TypeError, RecursionError):
            return 400, {"error": "invalid_request"}
        except (AmtronBusError, OSError):
            return _UNAVAILABLE

    async def _route(self, method: str, path: str, payload: object) -> dict[str, Any]:
        service = self._service
        if method == "GET":
            if path == "/v2/state":
                return await service.stable_snapshot(patient=True)
            if path == "/v2/diagnostics":
                # Passive RAM snapshot: no serial reads, writes or file access.
                return {
                    "write_transactions": service.client.write_trace.snapshot(),
                    "runtime_events": service.diagnostics.timeline.snapshot(),
                }
            raise _NotFoundError(path)
        if method != "POST" or path not in _POSTS:
            raise _NotFoundError(path)
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        if path == "/v2/soc-goal/reserve":
            if payload != {}:
                raise ValueError("invalid percentage-goal reservation")
            return {"token": service.soc_transactions.reserve()}
        if path == "/v2/soc-goal/resolve":
            if set(payload) != {"token"}:
                raise ValueError("invalid percentage-goal resolution")
            return await service.soc_transactions.resolve(payload["token"])
        if self._bus_requests.locked():
            raise ServiceBusyError
        if path == "/v2/soc-goal/commit":
            return await service.soc_transactions.commit(payload, gate=self._bus_requests)
        async with self._bus_requests:
            if path == "/v2/command":
                return await service.command(payload)
            return await service.read(payload)


_POSTS = frozenset({
    "/v2/command", "/v2/read", "/v2/soc-goal/reserve", "/v2/soc-goal/commit",
    "/v2/soc-goal/resolve",
})
