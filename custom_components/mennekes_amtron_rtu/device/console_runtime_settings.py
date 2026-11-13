"""Live runtime settings through the existing Supervisor save transaction."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .app_settings import AppSettings, SettingsError
from .errors import DeviceError
from .runtime_settings import runtime_candidate, runtime_preview
from .service_config import ServerConfig

if TYPE_CHECKING:
    from .service_console_runtime import ConsoleModbusService


class RuntimeSettingsConsole(AppSettings):
    """Reuse authorization, optimistic revisions and uncertain-save fencing."""

    def __init__(self, supervisor_token: str, service_token: str, *, service: ConsoleModbusService | None, **kwargs: Any) -> None:
        super().__init__(supervisor_token, service_token, **kwargs)
        self.service = service
        # This is an internal credential, never projected into any response.
        # Keep validation independent from changes to Supervisor's stored token.
        self._runtime_token = service_token

    def _configured_service(self) -> ConsoleModbusService:
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
