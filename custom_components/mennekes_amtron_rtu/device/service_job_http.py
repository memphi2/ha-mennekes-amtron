"""Charging choices share the authenticated command API and admin Ingress."""
from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from .console_charging import ChargingConsole
from .console_http import create_console_app
from .service_http import create_app

if TYPE_CHECKING:
    from .service_job_runtime import JobModbusService


def create_job_app(service: JobModbusService) -> web.Application:
    """Guest start is a named command; there is no separate order-edit API."""
    return create_app(service)


class JobConsole(ChargingConsole):
    """Use the same charging controls and limits as Home Assistant."""


def create_job_console(settings: JobConsole) -> web.Application:
    app = create_console_app(settings)
    app.router.add_get("/api/charging", settings.read_charging)
    app.router.add_post("/api/charging", settings.save_charging)
    return app
