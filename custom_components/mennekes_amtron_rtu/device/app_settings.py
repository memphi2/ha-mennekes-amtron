"""Connection setup through authenticated Supervisor ingress, without extra roles.

Only the Supervisor's real network peer is trusted. Supervisor 2026.11.0 is the
first verified release that enforces panel_admin and replaces every user/group
header; older releases cannot authorize this configuration page safely.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .service_config import ServerConfig

INGRESS_PORT = 8099
SUPERVISOR_URL = "http://supervisor"
SUPERVISOR_PEER = "172.30.32.2"
_TRANSPORT_LABELS = {
    "serial": "USB / RS-485",
    "rtu_over_tcp": "LAN: Modbus RTU (TCP)",
    "modbus_tcp": "LAN: Modbus TCP",
}
_TRANSPORTS = {**{label: key for key, label in _TRANSPORT_LABELS.items()}, **{key: key for key in _TRANSPORT_LABELS}}
_SERIAL_PATH = re.compile(r"/dev/(?:tty(?:USB|ACM|AMA|S|SC|THS|XRUSB)[0-9]+|serial/(?:by-id|by-path)/[^/\x00]+)")
_USER_ID = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"([0-9]{4})\.([0-9]{1,2})\.([0-9]+)")
_FIELDS = {"connection_type", "serial_port", "host", "tcp_port", "device_id", "baudrate", "frame"}
_ASSETS = {
    "/": ("app_settings.html", "text/html"),
    "/app_settings.js": ("app_settings.js", "text/javascript"),
    "/app_settings.css": ("app_settings.css", "text/css"),
}


def _language(header: str) -> str:
    """Use supported browser languages, without inserting untrusted header text."""
    for part in header[:1024].split(","):
        tag = part.split(";", 1)[0].strip().lower().split("-", 1)[0]
        if tag in {"de", "en", "fr", "nl", "it"}:
            return tag
    return "en"


@dataclass(frozen=True, slots=True)
class SetupConfig:
    """A live configuration interface with no Modbus connection or discovery."""

    listen: str
    port: int
    token: str = field(repr=False)


def _serial_path(value: object) -> str:
    if not isinstance(value, str) or not _SERIAL_PATH.fullmatch(value) or len(value) > 1024:
        raise ValueError("serial_port must select a supported local serial adapter")
    path = Path(value)
    # An unplugged real adapter remains a valid configuration. Reject an
    # existing non-device and aliases outside the serial-device allowlist.
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return value
    except OSError:
        raise ValueError("serial_port could not be checked") from None
    if not re.fullmatch(r"/dev/tty(?:USB|ACM|AMA|S|SC|THS|XRUSB)[0-9]+", str(resolved)) or not resolved.is_char_device():
        raise ValueError("serial_port must refer to a serial character device")
    return value


def app_configuration(values: dict[str, Any]) -> ServerConfig | SetupConfig:
    """Allow first setup only for missing connection fields after full validation.

    The probe never opens a connection. All token, permissions, timing, limits,
    serial framing and network-port validation still run before setup mode.
    """
    selection = values.get("connection_type", "serial")
    transport = _TRANSPORTS.get(selection) if isinstance(selection, str) else None
    probe = dict(values)
    missing = False
    if transport == "serial":
        port = values.get("serial_port")
        if port is None or port == "":
            probe["serial_port"] = "/dev/ttyUSB0"
            missing = True
        else:
            _serial_path(port)
    elif transport in ("rtu_over_tcp", "modbus_tcp") and values.get("host") in (None, ""):
        probe["host"] = "setup.invalid"
        missing = True
    config = ServerConfig.from_dict(probe)
    return SetupConfig(config.listen, config.port, config.token) if missing else config


@dataclass(frozen=True, slots=True)
class SerialAdapter:
    """One physical adapter, using its stable by-id path when available."""

    value: str
    label: str
    device: str


def serial_adapters() -> list[SerialAdapter]:
    """Enumerate locally mapped serial hardware; never request host privileges."""
    aliases: dict[str, str] = {}
    for directory in (Path("/dev/serial/by-path"), Path("/dev/serial/by-id")):
        if not directory.is_dir():
            continue
        for alias in sorted(directory.iterdir(), reverse=True):
            try:
                _serial_path(str(alias))
                if alias.is_char_device():
                    aliases[str(alias.resolve(strict=True))] = str(alias)
            except (OSError, ValueError):
                continue
    adapters: dict[str, SerialAdapter] = {}
    for path in Path("/dev").iterdir():
        if not re.fullmatch(r"tty(?:USB|ACM|AMA|S|SC|THS|XRUSB)[0-9]+", path.name):
            continue
        try:
            _serial_path(str(path))
            if not path.is_char_device():
                continue
            device = str(path.resolve(strict=True))
        except (OSError, ValueError):
            continue
        value = aliases.get(device, str(path))
        label = f"{Path(value).name} ({device})" if value != device else device
        adapters[device] = SerialAdapter(value, label, device)
    return sorted(adapters.values(), key=lambda item: item.value)


class SettingsError(Exception):
    """A public error code, never a Supervisor response or filesystem detail."""

    def __init__(self, key: str, status: int = 400) -> None:
        self.key, self.status = key, status
        super().__init__(key)


class AppSettings:
    """Read and replace only this app's connection settings via the self API."""

    def __init__(self, supervisor_token: str, service_token: str) -> None:
        self._supervisor_token = supervisor_token
        self._service_token = service_token
        self._secret = secrets.token_bytes(32)
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(2)

    async def lifecycle(self, _app: web.Application) -> AsyncIterator[None]:
        async with ClientSession(timeout=ClientTimeout(total=10), trust_env=False) as session:
            self._session = session
            try:
                yield
            finally:
                self._session = None

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._session is None:
            raise SettingsError("supervisor_unavailable", 503)
        async with self._session.request(
            method, SUPERVISOR_URL + path,
            headers={"Authorization": f"Bearer {self._supervisor_token}"},
            json=payload, allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise SettingsError("supervisor_unavailable", 503)
            raw = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise SettingsError("supervisor_unavailable", 503)
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError) as err:
                raise SettingsError("supervisor_unavailable", 503) from err
            if not isinstance(body, dict) or body.get("result") != "ok":
                raise SettingsError("supervisor_unavailable", 503)
            data = body.get("data")
            if not isinstance(data, dict):
                raise SettingsError("supervisor_unavailable", 503)
            return data

    async def _authorize(self, request: web.Request) -> str:
        info = await self._request("GET", "/info")
        version = info.get("supervisor")
        match = _VERSION.fullmatch(version) if isinstance(version, str) and len(version) <= 32 else None
        if match is None or not 1 <= int(match[2]) <= 12 or tuple(int(part) for part in match.groups()) < (2026, 11, 0):
            raise SettingsError("supervisor_update_required", 503)
        user = request.headers.get("X-Remote-User-Id", "")
        groups = request.headers.get("X-Remote-User-Groups", "").split(",")
        if not _USER_ID.fullmatch(user) or "system-admin" not in groups:
            raise SettingsError("admin_required", 403)
        return user

    def _digest(self, purpose: str, value: str) -> str:
        return hmac.new(self._secret, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    def _revision(self, options: dict[str, Any]) -> str:
        return self._digest("options", json.dumps(options, sort_keys=True, separators=(",", ":"), allow_nan=False))

    async def _options(self) -> dict[str, Any]:
        info = await self._request("GET", "/addons/self/info")
        options = info.get("options")
        if not isinstance(options, dict):
            raise SettingsError("supervisor_unavailable", 503)
        return options

    async def read(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        options = await self._options()
        adapters = await asyncio.to_thread(serial_adapters)
        settings = {key: options[key] for key in _FIELDS if key in options}
        settings["connection_type"] = _TRANSPORTS.get(settings.get("connection_type", "serial"), "serial")
        selected = settings.get("serial_port")
        if isinstance(selected, str) and _SERIAL_PATH.fullmatch(selected):
            resolved = await asyncio.to_thread(Path(selected).resolve)
            for adapter in adapters:
                if str(resolved) == adapter.device:
                    settings["serial_port"] = adapter.value
                    break
        return web.json_response({
            "settings": settings,
            "adapters": [{"value": adapter.value, "label": adapter.label} for adapter in adapters],
            "revision": self._revision(options),
            "csrf": self._digest("csrf", user),
        })

    async def save(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        csrf = request.headers.get("X-Amtron-CSRF", "")
        if not _DIGEST.fullmatch(csrf) or not hmac.compare_digest(csrf, self._digest("csrf", user)):
            raise SettingsError("reload_required", 403)
        if request.content_type != "application/json":
            raise SettingsError("invalid_request", 415)
        try:
            payload = await request.json()
        except (ValueError, RecursionError) as err:
            raise SettingsError("invalid_request") from err
        if not isinstance(payload, dict):
            raise SettingsError("invalid_request")
        transport = payload.get("connection_type")
        if not isinstance(transport, str) or transport not in _TRANSPORT_LABELS:
            raise SettingsError("invalid_request")
        keys = {"revision", "connection_type", "device_id"} | (
            {"serial_port", "baudrate", "frame"} if transport == "serial" else {"host", "tcp_port"}
        )
        if payload.keys() != keys:
            raise SettingsError("invalid_request")
        async with self._lock:
            options = await self._options()
            revision = payload["revision"]
            if not isinstance(revision, str) or not _DIGEST.fullmatch(revision) or not hmac.compare_digest(revision, self._revision(options)):
                raise SettingsError("settings_changed", 409)
            updated = {**options, **{key: value for key, value in payload.items() if key != "revision"}}
            updated["connection_type"] = _TRANSPORT_LABELS[transport]
            if transport == "serial":
                adapters = await asyncio.to_thread(serial_adapters)
                if not isinstance(payload["serial_port"], str) or payload["serial_port"] not in {adapter.value for adapter in adapters}:
                    raise SettingsError("adapter_missing")
            else:
                updated.pop("serial_port", None)
            try:
                config = await asyncio.to_thread(app_configuration, {**updated, "token": self._service_token})
            except (OSError, ValueError) as err:
                raise SettingsError("connection_invalid") from err
            if isinstance(config, SetupConfig):
                raise SettingsError("connection_invalid")
            await self._request("POST", "/addons/self/options", {"options": updated})
        return web.json_response({"saved": True, "restart_required": True, "revision": self._revision(updated)})

    @web.middleware
    async def guard(
        self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        peer = request.transport.get_extra_info("peername") if request.transport is not None else None
        if not isinstance(peer, tuple) or not peer or peer[0] != SUPERVISOR_PEER:
            raise web.HTTPForbidden
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise web.HTTPForbidden
        try:
            if self._slots.locked():
                raise SettingsError("busy", 503)
            async with self._slots:
                response = await handler(request)
        except SettingsError as err:
            response = web.json_response({"error": err.key}, status=err.status)
        except (ClientError, TimeoutError, OSError, ValueError, TypeError):
            response = web.json_response({"error": "supervisor_unavailable"}, status=503)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
        })
        return response


def create_settings_app(settings: AppSettings) -> web.Application:
    """Expose only fixed assets and the two connection-configuration routes."""
    app = web.Application(middlewares=[settings.guard], client_max_size=4096)
    app.cleanup_ctx.append(settings.lifecycle)

    async def asset(request: web.Request) -> web.Response:
        filename, content_type = _ASSETS[request.path]
        resource = files(__package__).joinpath(filename)
        content = await asyncio.to_thread(resource.read_bytes)
        if content_type == "text/html":
            language = _language(request.headers.get("Accept-Language", ""))
            content = content.replace(b'lang="en"', f'lang="{language}"'.encode(), 1)
        return web.Response(body=content, content_type=content_type)

    for path in _ASSETS:
        app.router.add_get(path, asset)
    app.router.add_get("/api/config", settings.read)
    app.router.add_post("/api/config", settings.save)
    return app


def create_setup_app(config: SetupConfig) -> web.Application:
    """Keep watchdog liveness truthful while no wallbox connection is configured."""
    @web.middleware
    async def authenticate(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.path != "/health" and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {config.token}".encode(),
        ):
            raise web.HTTPUnauthorized
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=4096)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "running", "configured": False})

    async def unavailable(_request: web.Request) -> web.Response:
        return web.json_response({"error": "service_not_configured"}, status=503)

    app.router.add_get("/health", health)
    app.router.add_route("*", "/v2/{path:.*}", unavailable)
    return app
