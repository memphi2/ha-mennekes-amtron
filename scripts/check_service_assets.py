"""Serve installed app assets on loopback without Supervisor or hardware access.

The image smoke injects this probe into a separate process in the running test
container. Only this probe's settings object trusts loopback; the real service's
Supervisor-peer check and its processes remain unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, web

ASSETS = {
    "/": ("app_settings.html", "text/html"),
    "/app_settings.js": ("app_settings.js", "text/javascript"),
    "/app_settings.css": ("app_settings.css", "text/css"),
}


class ProbeSettings:
    """Permit only local static reads; never contact Supervisor or serial ports."""

    async def lifecycle(self, _app: web.Application) -> AsyncIterator[None]:
        yield

    @web.middleware
    async def guard(
        self, request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.remote != "127.0.0.1" or request.path not in ASSETS or request.method != "GET":
            raise web.HTTPForbidden
        return await handler(request)

    async def read(self, _request: web.Request) -> web.Response:
        raise web.HTTPForbidden

    save = read
    inspect_adapter = read
    read_modbus_debug = read
    set_modbus_debug = read


async def check_assets(expected: dict[str, str]) -> None:
    """Check the installed package's actual HTTP handlers and exact source bytes."""
    if expected.keys() != ASSETS.keys():
        raise ValueError("expected hashes for all three app assets")
    settings_module = importlib.import_module("amtron_device.app_settings")
    runner = web.AppRunner(settings_module.create_settings_app(ProbeSettings()), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]
        async with ClientSession(timeout=ClientTimeout(total=5), trust_env=False) as session:
            for path, (_, content_type) in ASSETS.items():
                async with session.get(f"http://127.0.0.1:{port}{path}", allow_redirects=False) as response:
                    content = await response.read()
                    if (
                        response.status != 200 or response.content_type != content_type
                        or hashlib.sha256(content).hexdigest() != expected[path]
                    ):
                        raise ValueError(f"installed app asset differs or cannot be served: {path}")
    finally:
        await runner.cleanup()


def main() -> int:
    try:
        asyncio.run(check_assets(json.loads(sys.argv[1])))
    except (ValueError, OSError) as error:
        sys.stderr.write(f"App asset validation failed: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
