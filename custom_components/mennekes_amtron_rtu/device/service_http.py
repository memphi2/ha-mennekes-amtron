"""Versioned local API: authenticated commands, no arbitrary register writes."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from aiohttp import web

from .service_api import GETS, POSTS, ServiceApi, ServiceBusyError
from .service_runtime import ModbusService


def create_app(service: ModbusService) -> web.Application:
    api = ServiceApi(service)

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
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=4096)

    async def health(_request: web.Request) -> web.Response:
        # Process liveness only. USB loss must not cause a restart loop.
        return web.json_response({"status": "running"})

    async def call(request: web.Request) -> web.Response:
        payload: object = None
        if request.method == "POST":
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType
            try:
                payload = await request.json()
            except (ValueError, RecursionError):
                return web.json_response({"error": "invalid_request"}, status=400)
        try:
            # A route for GET answers HEAD as well.
            method = "GET" if request.method == "HEAD" else request.method
            status, body = await api.dispatch(method, request.path, payload)
        except ServiceBusyError:
            raise web.HTTPServiceUnavailable from None
        return web.json_response(body, status=status)

    app.router.add_get("/health", health)
    for path in GETS:
        app.router.add_get(path, call)
    for path in POSTS:
        app.router.add_post(path, call)
    return app
