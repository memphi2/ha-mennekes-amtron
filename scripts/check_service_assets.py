"""Serve installed app assets on loopback without Supervisor or hardware access.

The image smoke injects this probe into a separate process in the running test
container. Only this probe's asset-only middleware trusts loopback; the real service's
Supervisor-peer check and its processes remain unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import sys
from collections.abc import Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, web

# The routes the installed package registers; filled from the package itself,
# so a new page needs no second table here or on the host.
ASSETS: dict[str, tuple[str, str]] = {}


@web.middleware
async def local_assets_only(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Permit only local static reads, without any settings API or lifecycle."""
    if request.remote != "127.0.0.1" or request.path not in ASSETS or request.method != "GET":
        raise web.HTTPForbidden
    return await handler(request)


async def check_assets(expected: dict[str, str]) -> None:
    """Serve every registered asset and compare it with the source by file name.

    ``expected`` maps each app asset file of the source tree to its hash. A
    file without a route fails as well as a route without its file.
    """
    if not expected:
        raise ValueError("expected hashes for all registered app assets")
    settings_module = importlib.import_module("amtron_device.app_settings")
    console_module = importlib.import_module("amtron_device.console_http")
    ASSETS.clear()
    ASSETS.update(settings_module._ASSETS)  # noqa: SLF001 - the package's own route table
    ASSETS.update(console_module.CONSOLE_ASSETS)
    if {filename for filename, _type in ASSETS.values()} != expected.keys():
        raise ValueError("expected hashes for all registered app assets")
    app = web.Application(middlewares=[local_assets_only])
    settings_module.add_settings_asset_routes(app)
    console_module.add_console_asset_routes(app)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]
        async with ClientSession(timeout=ClientTimeout(total=5), trust_env=False) as session:
            for path, (filename, content_type) in ASSETS.items():
                async with session.get(f"http://127.0.0.1:{port}{path}", allow_redirects=False) as response:
                    content = await response.read()
                    if (
                        response.status != 200 or response.content_type != content_type
                        or hashlib.sha256(content).hexdigest() != expected[filename]
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
