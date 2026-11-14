"""Bounded job commands through existing authenticated API and admin Ingress."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .app_settings import SettingsError
from .charge_job_controller import ChargeJobController, JobRequestError
from .console_charging import ChargingConsole
from .console_http import create_console_app
from .guest_draft import defaults
from .service_http import create_app

if TYPE_CHECKING:
    from .service_job_runtime import JobModbusService


def job_controller(service: JobModbusService | None) -> ChargeJobController:
    if service is None or service.jobs is None:
        raise JobRequestError("job_service_starting")
    return service.jobs


async def job_operation(controller: ChargeJobController, payload: object) -> dict[str, Any]:
    """An explicit action allowlist; neither arbitrary writes nor auto retries."""
    if not isinstance(payload, dict):
        raise JobRequestError("invalid_job_request")
    action = payload.get("action")
    if action is not None and not isinstance(action, str):
        raise ValueError("invalid action type")
    if action == "reset_guest_draft" and payload.keys() == {"action", "draft_revision", "confirm"} and payload["confirm"] is True:
        return await controller.edit_draft(defaults(), revision=payload["draft_revision"])
    if action == "edit_guest_draft" and payload.keys() == {"action", "draft_revision", "changes"}:
        return await controller.edit_draft(payload["changes"], revision=payload["draft_revision"])
    if action == "preview_guest_draft" and payload.keys() == {"action", "draft_revision", "revision", "scope"}:
        return controller.preview_draft(draft_revision=payload["draft_revision"], revision=payload["revision"], scope=payload["scope"])
    if action == "create_guest_draft" and payload.keys() == {"action", "draft_revision", "revision", "scope", "request"}:
        return await controller.create_draft(payload["request"], draft_revision=payload["draft_revision"], revision=payload["revision"], scope=payload["scope"])
    if action == "preview_guest" and payload.keys() == {"action", "revision", "scope", "request"}:
        return controller.preview_guest(payload["request"], revision=payload["revision"], scope=payload["scope"])
    if action == "create" and payload.keys() == {"action", "revision", "scope", "request"}:
        if not isinstance(payload["request"], dict) or payload["request"].get("kind") != "guest":
            raise ValueError("invalid guest request")
        return await controller.create(payload["request"], revision=payload["revision"], scope=payload["scope"])
    if action in ("stop", "clear") and payload.keys() == {"action", "revision", "confirm"} and payload["confirm"] is True:
        if action == "stop":
            return await controller.stop(revision=payload["revision"])
        return await controller.clear(revision=payload["revision"])
    raise JobRequestError("invalid_job_request")


def create_job_app(service: JobModbusService) -> web.Application:
    app = create_app(service)
    slots = asyncio.Semaphore(4)

    async def jobs(request: web.Request) -> web.Response:
        try:
            if request.query:
                raise JobRequestError("invalid_job_request")
            controller = job_controller(service)
            if request.method == "GET":
                return web.json_response(controller.snapshot(), headers={"Cache-Control": "no-store"})
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType
            if slots.locked():
                raise web.HTTPServiceUnavailable
            payload = await request.json()
            if isinstance(payload, dict) and "expected_serial_number" in payload:
                # Home Assistant names the wallbox it is bound to. An app that
                # now drives another one refuses before any order changes.
                payload = dict(payload)
                if payload.pop("expected_serial_number") != service.identity.serial_number:
                    raise JobRequestError("device_identity_changed")
            async with slots:
                result = await job_operation(controller, payload)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except JobRequestError as err:
            return web.json_response({"error": str(err)}, status=409)
        except (ValueError, TypeError, RecursionError):
            return web.json_response({"error": "invalid_job_request"}, status=400)

    app.router.add_get("/v2/jobs", jobs)
    app.router.add_post("/v2/jobs", jobs)
    return app


class JobConsole(ChargingConsole):
    """Job writes inherit Supervisor peer, administrator, CSRF and size checks."""

    def __init__(self, *args: Any, service: JobModbusService | None, **kwargs: Any) -> None:
        super().__init__(*args, service=service, **kwargs)
        self.job_service = service

    async def read_jobs(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        if request.query:
            raise SettingsError("invalid_job_request")
        try:
            controller = job_controller(self.job_service)
        except JobRequestError as err:
            raise SettingsError(str(err), 503) from err
        return web.json_response({**controller.snapshot(), "csrf": self._digest("csrf", user)})

    async def edit_jobs(self, request: web.Request) -> web.Response:
        _, payload = await self._edit_request(request)
        if request.query:
            raise SettingsError("invalid_job_request")
        try:
            return web.json_response(await job_operation(job_controller(self.job_service), payload))
        except JobRequestError as err:
            raise SettingsError(str(err), 409) from err
        except (ValueError, TypeError, RecursionError) as err:
            raise SettingsError("invalid_job_request") from err


def create_job_console(settings: JobConsole) -> web.Application:
    app = create_console_app(settings)
    app.router.add_get("/api/charging", settings.read_charging)
    app.router.add_post("/api/charging", settings.save_charging)
    app.router.add_get("/api/jobs", settings.read_jobs)
    app.router.add_post("/api/jobs", settings.edit_jobs)
    return app
