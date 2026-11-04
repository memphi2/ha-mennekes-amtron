"""Run as `python -m amtron_device.server --config /data/options.json`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from contextlib import suppress
from pathlib import Path

from aiohttp import web

from .service_config import ServerConfig
from .service_discovery import app_token, publish_discovery
from .service_http import create_app
from .service_runtime import ModbusService


async def run(config: ServerConfig, state: Path, supervisor_token: str = "") -> None:
    service = ModbusService(config, state)
    runner = web.AppRunner(create_app(service), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.listen, config.port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    discovery: asyncio.Task[None] | None = None
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await site.start()
        service.start()
        if supervisor_token:
            discovery = asyncio.create_task(publish_discovery(config, supervisor_token))
        await stop.wait()
    finally:
        if discovery is not None:
            discovery.cancel()
            with suppress(asyncio.CancelledError):
                await discovery
        await runner.cleanup()
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--state", type=Path, default=Path("/data/intent.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    try:
        values = json.loads(args.config.read_text())
        if not isinstance(values, dict):
            raise ValueError("configuration must be an object")
        if supervisor_token and values.get("token", "") == "":
            values["token"] = app_token(args.state.with_name("service-token"))
        config = ServerConfig.from_dict(values)
    except ValueError, OSError:
        parser.exit(
            2, "Invalid or unreadable service configuration; check the options.\n"
        )
    asyncio.run(run(config, args.state, supervisor_token))


if __name__ == "__main__":
    main()
