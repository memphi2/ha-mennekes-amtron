"""Run as `python -m amtron_device.server --config /data/options.json`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from pathlib import Path

from aiohttp import web

from .service_config import ServerConfig
from .service_http import create_app
from .service_runtime import ModbusService


async def run(config: ServerConfig, state: Path) -> None:
    service = ModbusService(config, state)
    runner = web.AppRunner(create_app(service), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.listen, config.port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await site.start()
        service.start()
        await stop.wait()
    finally:
        await runner.cleanup()
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--state", type=Path, default=Path("/data/intent.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        values = json.loads(args.config.read_text())
        if not isinstance(values, dict):
            raise ValueError("configuration must be an object")
        config = ServerConfig.from_dict(values)
    except ValueError, OSError:
        parser.exit(
            2, "Invalid or unreadable service configuration; check the options.\n"
        )
    asyncio.run(run(config, args.state))


if __name__ == "__main__":
    main()
