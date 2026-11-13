"""Versioned local API: authenticated commands, no arbitrary register writes."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from .client_errors import AmtronBusError
from .errors import DeviceError, DeviceValidationError
from .service_runtime import ModbusService


def create_app(service: ModbusService) -> web.Application:
    bus_requests = asyncio.Semaphore(8)

    @web.middleware
    async def authenticate(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.path != "/health" and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(),
            f"Bearer {service.config.token}".encode(),
        ):
            raise web.HTTPUnauthorized
        try:
            return await handler(request)
        except DeviceError as err:
            return web.json_response(
                {
                    "error": err.translation_key,
                    "placeholders": err.translation_placeholders,
                    "validation": isinstance(err, DeviceValidationError),
                },
                status=409,
            )
        except ValueError, TypeError:
            return web.json_response({"error": "invalid_request"}, status=400)
        except AmtronBusError, OSError:
            return web.json_response({"error": "service_unavailable"}, status=503)

    app = web.Application(middlewares=[authenticate], client_max_size=4096)

    async def health(_request: web.Request) -> web.Response:
        # Process liveness only. USB loss must not cause a restart loop.
        return web.json_response({"status": "running"})

    async def state(_request: web.Request) -> web.Response:
        return web.json_response(await service.stable_snapshot(patient=True))

    async def diagnostics(_request: web.Request) -> web.Response:
        # Passive RAM snapshot: no serial reads, writes or file access.
        return web.json_response({
            "write_transactions": service.client.write_trace.snapshot(),
            "runtime_events": service.diagnostics.timeline.snapshot(),
        })

    async def body(request: web.Request) -> dict[str, Any]:
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        return payload

    async def command(request: web.Request) -> web.Response:
        if bus_requests.locked():
            raise web.HTTPServiceUnavailable
        async with bus_requests:
            return web.json_response(await service.command(await body(request)))

    async def read(request: web.Request) -> web.Response:
        if bus_requests.locked():
            raise web.HTTPServiceUnavailable
        async with bus_requests:
            return web.json_response(await service.read(await body(request)))

    async def soc_reserve(request: web.Request) -> web.Response:
        if await body(request) != {}:
            raise ValueError("invalid percentage-goal reservation")
        return web.json_response({"token": service.soc_transactions.reserve()})

    async def soc_commit(request: web.Request) -> web.Response:
        if bus_requests.locked():
            raise web.HTTPServiceUnavailable
        return web.json_response(await service.soc_transactions.commit(await body(request), gate=bus_requests))

    async def soc_resolve(request: web.Request) -> web.Response:
        payload = await body(request)
        if set(payload) != {"token"}:
            raise ValueError("invalid percentage-goal resolution")
        return web.json_response(await service.soc_transactions.resolve(payload["token"]))

    app.router.add_get("/health", health)
    app.router.add_get("/v2/state", state)
    app.router.add_get("/v2/diagnostics", diagnostics)
    app.router.add_post("/v2/command", command)
    app.router.add_post("/v2/read", read)
    app.router.add_post("/v2/soc-goal/reserve", soc_reserve)
    app.router.add_post("/v2/soc-goal/commit", soc_commit)
    app.router.add_post("/v2/soc-goal/resolve", soc_resolve)
    return app
