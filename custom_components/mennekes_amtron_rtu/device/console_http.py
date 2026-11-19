"""Service console behind the existing Supervisor-peer and admin boundary."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .app_settings import AppSettings, SettingsError, create_settings_app, serve_asset
from .client_errors import AmtronBusError, AmtronRateLimitedError
from .errors import DeviceError
from .runtime_settings import runtime_candidate, runtime_preview
from .service_config import ServerConfig

if TYPE_CHECKING:
    from .service_runtime import ModbusService

CONSOLE_ASSETS = {
    "/targets": ("app_targets.html", "text/html"),
    "/app_charging.js": ("app_charging.js", "text/javascript"),
    "/registers": ("app_registers.html", "text/html"),
    "/runtime": ("app_runtime.html", "text/html"),
    "/app_runtime.js": ("app_runtime.js", "text/javascript"),
    "/app_registers.js": ("app_registers.js", "text/javascript"),
}

# Refusals decided before the requested change, each with its own app hint.
# Everything else, e.g. a lost write or storage error, stays uncertain.
_REFUSALS = frozenset({
    "soc_goal_source_changed", "soc_goal_limit_unavailable", "read_only_mode",
    "guest_start_not_permitted", "guest_deadline_expired", "time_limit_reached",
    "energy_goal_stop_active", "guest_confirmation_required",
    "energy_goal_invalid", "soc_goal_invalid",
    "energy_goal_unavailable", "cost_goal_invalid", "cost_goal_price_unknown",
    "energy_goal_not_supported", "target_time_not_future", "write_rate_limited",
})


class ServiceConsole(AppSettings):
    """Admin-only operations on the running service, next to the app settings.

    Reuses authorization, optimistic revisions and uncertain-save fencing; only
    explicitly registered routes extend the configuration surface.
    """

    def __init__(self, supervisor_token: str, service_token: str, *, service: ModbusService | None, **kwargs: Any) -> None:
        super().__init__(supervisor_token, service_token, **kwargs)
        self.service = service
        # This is an internal credential, never projected into any response.
        # Keep validation independent from changes to Supervisor's stored token.
        self._runtime_token = service_token

    def _configured_service(self) -> ModbusService:
        if self.service is None:
            raise SettingsError("service_not_configured", 503)
        return self.service

    def _saved_config(self, options: dict[str, Any]) -> ServerConfig:
        try:
            return ServerConfig.from_dict({**options, "token": self._runtime_token})
        except ValueError as err:
            raise SettingsError("runtime_settings_invalid") from err

    async def read_runtime(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        if request.query:
            raise SettingsError("invalid_request")
        service = self._configured_service()
        options = await self._options()
        saved = self._saved_config(options)
        return web.json_response({
            "schema": 1, **runtime_preview(service.config, saved, saved),
            "revision": self._revision(options), "csrf": self._digest("csrf", user),
            "write_blocked": self._options_write_uncertain,
        })

    async def preview_runtime(self, request: web.Request) -> web.Response:
        return await self._runtime_edit(request, save=False)

    async def save_runtime(self, request: web.Request) -> web.Response:
        return await self._runtime_edit(request, save=True)

    async def _runtime_edit(self, request: web.Request, *, save: bool) -> web.Response:
        _, payload = await self._edit_request(request)
        if request.query:
            raise SettingsError("invalid_request")
        expected = {"revision", "changes", "confirm"} if save else {"revision", "changes"}
        if payload.keys() != expected or (save and payload["confirm"] is not True):
            raise SettingsError("invalid_request")
        service = self._configured_service()
        async with self._lock:
            if self._options_write_uncertain:
                raise SettingsError("options_save_uncertain", 503)
            options = await self._options()
            self._check_revision(payload["revision"], options)
            original = self._saved_config(options)
            try:
                updated, candidate = runtime_candidate(options, payload["changes"], token=self._runtime_token)
            except ValueError as err:
                raise SettingsError("runtime_settings_invalid") from err
            result = runtime_preview(service.config, original, candidate)
            if not save:
                return web.json_response({"schema": 1, **result, "revision": self._revision(options)})
            # Native Supervisor edits do not share this process's lock.
            if await self._options() != options:
                raise SettingsError("settings_changed", 409)
            await self._persist_options(options, updated, "options_save_uncertain")
            try:
                async with asyncio.timeout(2):
                    await service.apply_runtime_settings(candidate)
            except (TimeoutError, DeviceError) as err:
                # Saved state may already be durable. Report uncertainty rather
                # than rolling back over another administrator's newer changes.
                raise SettingsError("runtime_apply_pending", 503) from err
            return web.json_response({
                "schema": 1, **runtime_preview(service.config, candidate, candidate),
                "saved_ok": True, "revision": self._revision(updated),
            })

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

    def _choices(self) -> dict[str, Any]:
        service = self._configured_service()
        control = service.control
        if control is None:
            raise SettingsError("service_not_configured", 503)
        departure = getattr(service, "departure", None)
        plan = departure.snapshot() if departure is not None and departure.ready else None
        runner = control.goal_controller
        return {
            "schema": 1, "read_only": control.read_only,
            "revision": service.choice_revision,
            "guest_revision": service.guest.revision,
            "guest_active": service.guest.active,
            "energy": {"target": control.energy_goal.target_kwh, "status": control.energy_goal.status},
            # An amount of money carried by the energy target, and the last
            # price Home Assistant reported (none without charging costs).
            "cost": None if (cost := control.energy_goal.cost) is None else {
                "amount": cost.amount, "spent": control.energy_goal.cost_spent, "price": cost.price,
            },
            "cost_price": None if runner is None else runner.price,
            # When Home Assistant last reported the price, alone or with an
            # amount.
            "cost_price_at": None if runner is None or runner.price_at is None else runner.price_at.isoformat(),
            "guest_confirmation_required": service.guest.restore_pending,
            "soc": {
                "configured": control.soc_goal.source is not None,
                "target": control.soc_goal.target_percent,
                "maximum": control.soc_goal.maximum_percent,
                "maximum_required": service.snapshot()["control"]["soc_maximum_required"],
                "status": control.soc_goal.status,
            },
            "departure": None if plan is None or departure is None else {
                "revision": departure.choice_revision,
                "configured": True,
                "deadline": plan["trip"]["deadline"], "status": plan["status"],
                "planned_start": plan["planned_start"],
                "storage_error": departure.storage_error,
            },
        }

    async def read_charging(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        if request.query:
            raise SettingsError("invalid_request")
        await self._configured_service().stable_snapshot()
        return web.json_response({**self._choices(), "csrf": self._digest("csrf", user)})

    async def save_charging(self, request: web.Request) -> web.Response:
        user, payload = await self._edit_request(request)
        if request.query or payload.keys() != {"action", "value", "revision", "confirm"} or payload["confirm"] is not True:
            raise SettingsError("invalid_request")
        service = self._configured_service()
        if not isinstance(payload["revision"], str):
            raise SettingsError("invalid_request")
        action = payload["action"]
        try:
            if action in ("energy", "soc"):
                await service.command({"origin": "app",
                    "command": "energy_goal" if action == "energy" else "soc_goal_choice",
                    "value": payload["value"], "expected_choice_revision": payload["revision"],
                    "expected_serial_number": service.identity.serial_number,
                })
            elif action == "cost":
                await service.command({"origin": "app",
                    "command": "cost_goal", "value": payload["value"],
                    "expected_choice_revision": payload["revision"],
                    "expected_serial_number": service.identity.serial_number,
                })
            elif action == "guest":
                if payload["value"] is not True:
                    raise ValueError("guest start must be explicit")
                await service.command({"origin": "app",
                    "command": "guest_start", "expected_guest_revision": payload["revision"],
                    "expected_serial_number": service.identity.serial_number,
                })
            elif action == "pause":
                if payload["value"] is not True:
                    raise ValueError("pause must be explicit")
                await service.command({"origin": "app",
                    "command": "pause", "value": True,
                    "expected_serial_number": service.identity.serial_number,
                })
            elif action == "departure":
                await service.command({"origin": "app",
                    "command": "departure_choice", "deadline": payload["value"],
                    "revision": payload["revision"],
                    "expected_serial_number": service.identity.serial_number,
                })
            else:
                raise SettingsError("invalid_request")
        except DeviceError as err:
            # Codes only; never expose source IDs, credentials or arbitrary errors.
            code = str(err)
            # An applied guest start fails as guest_start_uncertain, no refusal.
            safe = code if code in _REFUSALS else "charging_save_uncertain"
            raise SettingsError(safe, 409) from err
        except (ValueError, TypeError, OverflowError) as err:
            raise SettingsError("invalid_request") from err
        except AmtronRateLimitedError as err:
            # Refused before anything was written: it has to wait itself.
            # That holds for a guest start, too; the queue refuses a command
            # of several steps before its first write.
            raise SettingsError("write_rate_limited", 409) from err
        except (AmtronBusError, OSError) as err:
            # The wallbox or the disk failed after the command went out.
            raise SettingsError("charging_save_uncertain", 503) from err
        return web.json_response({**self._choices(), "csrf": self._digest("csrf", user)})


def add_console_asset_routes(app: web.Application) -> None:
    """Static assets inherit the caller's middleware; never create a public port."""
    async def asset(request: web.Request) -> web.Response:
        return await serve_asset(request, CONSOLE_ASSETS)

    for path in CONSOLE_ASSETS:
        app.router.add_get(path, asset)


def create_console_app(settings: ServiceConsole) -> web.Application:
    app = create_settings_app(settings)
    add_console_asset_routes(app)
    app.router.add_get("/api/registers", settings.read_registers)
    app.router.add_get("/api/runtime", settings.read_runtime)
    app.router.add_post("/api/runtime/preview", settings.preview_runtime)
    app.router.add_post("/api/runtime", settings.save_runtime)
    app.router.add_get("/api/charging", settings.read_charging)
    app.router.add_post("/api/charging", settings.save_charging)
    return app
