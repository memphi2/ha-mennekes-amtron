"""Charging choices share the authenticated command API and admin Ingress."""
from __future__ import annotations

from aiohttp import web

from .console_charging import ChargingConsole
from .console_http import create_console_app


def create_charging_console(settings: ChargingConsole) -> web.Application:
    app = create_console_app(settings)
    app.router.add_get("/api/charging", settings.read_charging)
    app.router.add_post("/api/charging", settings.save_charging)
    return app
