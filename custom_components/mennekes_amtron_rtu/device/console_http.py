"""Service console behind the existing Supervisor-peer and admin boundary."""

from __future__ import annotations

import asyncio
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .app_settings import SettingsError, create_settings_app
from .console_runtime_settings import RuntimeSettingsConsole

if TYPE_CHECKING:
    from .service_console_runtime import ConsoleModbusService

CONSOLE_ASSETS = {
    "/guest": ("app_guest.html", "text/html"),
    "/app_guest.js": ("app_guest.js", "text/javascript"),
    "/jobs": ("app_jobs.html", "text/html"),
    "/app_jobs.js": ("app_jobs.js", "text/javascript"),
    "/runtime": ("app_runtime.html", "text/html"),
    "/app_runtime.js": ("app_runtime.js", "text/javascript"),
    "/registers": ("app_registers.html", "text/html"),
    "/app_registers.js": ("app_registers.js", "text/javascript"),
}


class ServiceConsole(RuntimeSettingsConsole):
    """Only explicitly registered operations extend the configuration surface."""

    def __init__(self, *args: Any, service: ConsoleModbusService | None, **kwargs: Any) -> None:
        super().__init__(*args, service=service, **kwargs)

    async def read_registers(self, request: web.Request) -> web.Response:
        await self._authorize(request)
        if request.query:
            raise SettingsError("invalid_request")
        service = self.service
        if service is None:
            raise SettingsError("service_not_configured", 503)
        async with asyncio.timeout(2):
            state = await service.stable_snapshot()
        data = service.register_monitor.snapshot(
            layout=service.identity.layout_version if service.control is not None else None,
            available=state["available"], scan_interval=service.config.scan_interval,
            measurement_interval=service.config.measurement_interval,
            idle=service.data.fresh("evse_state") == 1,
        )
        return web.json_response(data)


def add_console_asset_routes(app: web.Application) -> None:
    """Static assets inherit the caller's middleware; never create a public port."""
    async def asset(request: web.Request) -> web.Response:
        filename, content_type = CONSOLE_ASSETS[request.path]
        resource = files(__package__).joinpath(filename)
        return web.Response(body=await asyncio.to_thread(resource.read_bytes), content_type=content_type)

    for path in CONSOLE_ASSETS:
        app.router.add_get(path, asset)


def create_console_app(settings: ServiceConsole) -> web.Application:
    app = create_settings_app(settings)
    add_console_asset_routes(app)
    app.router.add_get("/api/registers", settings.read_registers)
    app.router.add_get("/api/runtime", settings.read_runtime)
    app.router.add_post("/api/runtime/preview", settings.preview_runtime)
    app.router.add_post("/api/runtime", settings.save_runtime)
    return app
