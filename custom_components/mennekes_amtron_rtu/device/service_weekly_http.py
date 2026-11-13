"""Weekly recipes through existing bearer and administrator/CSRF boundaries."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .app_settings import SettingsError
from .charge_job_controller import JobRequestError
from .service_job_http import JobConsole, create_job_app, create_job_console
from .weekly_controller import WeeklyController, WeeklyError

if TYPE_CHECKING:
    from .service_weekly_runtime import WeeklyModbusService

def weekly_controller(service: WeeklyModbusService | None) -> WeeklyController:
    if service is None or service.weekly is None:
        raise WeeklyError("weekly_service_starting")
    return service.weekly


async def weekly_operation(controller: WeeklyController, payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WeeklyError("invalid_weekly_request")
    action = payload.get("action")
    if action == "preview_plan" and payload.keys() == {"action", "revision", "plan"}:
        return controller.preview_plan(payload["plan"], revision=payload["revision"])
    if action == "save_plan" and payload.keys() == {"action", "revision", "plan", "confirm"} and payload["confirm"] is True:
        return await controller.save_plan(payload["plan"], revision=payload["revision"])
    if action == "remove_plan" and payload.keys() == {"action", "revision", "plan_id", "confirm"} and payload["confirm"] is True:
        return await controller.remove_plan(payload["plan_id"], revision=payload["revision"])
    if action == "preview_activation" and payload.keys() == {"action", "revision", "plan_id", "vehicle_profile", "scope"}:
        return controller.preview_activation(payload["plan_id"], payload["vehicle_profile"],
                                             revision=payload["revision"], scope=payload["scope"])
    if action == "activate" and payload.keys() == {"action", "proposal", "confirm"} and payload["confirm"] is True:
        return await controller.activate(payload["proposal"])
    if action == "deactivate" and payload.keys() == {"action", "revision", "confirm"} and payload["confirm"] is True:
        return await controller.deactivate(revision=payload["revision"])
    raise WeeklyError("invalid_weekly_request")


def create_weekly_app(service: WeeklyModbusService) -> web.Application:
    app = create_job_app(service)
    slots = asyncio.Semaphore(4)

    async def weekly(request: web.Request) -> web.Response:
        try:
            if request.query:
                raise WeeklyError("invalid_weekly_request")
            controller = weekly_controller(service)
            if request.method == "GET":
                return web.json_response(controller.snapshot(), headers={"Cache-Control": "no-store"})
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType
            if slots.locked():
                raise web.HTTPServiceUnavailable
            async with slots:
                result = await weekly_operation(controller, await request.json())
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (WeeklyError, JobRequestError) as err:
            return web.json_response({"error": str(err)}, status=409, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError, RecursionError, OverflowError):
            return web.json_response({"error": "invalid_weekly_request"}, status=400, headers={"Cache-Control": "no-store"})

    app.router.add_get("/v2/weekly", weekly)
    app.router.add_post("/v2/weekly", weekly)
    return app


class WeeklyConsole(JobConsole):
    """No new role, public listener, permission or direct Modbus operation."""

    def __init__(self, *args: Any, service: WeeklyModbusService | None, **kwargs: Any) -> None:
        super().__init__(*args, service=service, **kwargs)
        self.weekly_service = service

    async def read_weekly(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        if request.query:
            raise SettingsError("invalid_weekly_request")
        try:
            controller = weekly_controller(self.weekly_service)
        except WeeklyError as err:
            raise SettingsError(str(err), 503) from err
        return web.json_response({**controller.snapshot(), "csrf": self._digest("csrf", user)})

    async def edit_weekly(self, request: web.Request) -> web.Response:
        _, payload = await self._edit_request(request)
        if request.query:
            raise SettingsError("invalid_weekly_request")
        try:
            return web.json_response(await weekly_operation(weekly_controller(self.weekly_service), payload))
        except (WeeklyError, JobRequestError) as err:
            raise SettingsError(str(err), 409) from err
        except (ValueError, TypeError, RecursionError, OverflowError) as err:
            raise SettingsError("invalid_weekly_request") from err


def create_weekly_console(settings: WeeklyConsole) -> web.Application:
    # The pages themselves are console assets, served and localized there.
    app = create_job_console(settings)
    app.router.add_get("/api/weekly", settings.read_weekly)
    app.router.add_post("/api/weekly", settings.edit_weekly)
    return app
