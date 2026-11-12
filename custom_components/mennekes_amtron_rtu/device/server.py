"""Run as `python -m amtron_device.server --config /data/options.json`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

from aiohttp import web

from .app_settings import (
    INGRESS_PORT,
    AppSettings,
    SetupConfig,
    app_configuration,
    create_settings_app,
    create_setup_app,
)
from .connection import NetworkConfig, SerialConfig
from .service_config import ServerConfig
from .service_discovery import app_token, publish_discovery
from .service_http import create_app
from .service_overview import service_overview
from .service_runtime import ModbusService

_LOGGER = logging.getLogger(__name__)


async def run(config: ServerConfig | SetupConfig, state: Path, supervisor_token: str = "") -> None:
    service: ModbusService | None
    if isinstance(config, ServerConfig):
        service = ModbusService(config, state)
        api = create_app(service)
    else:
        service = None
        api = create_setup_app(config)
    runner = web.AppRunner(api, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.listen, config.port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    discovery: asyncio.Task[None] | None = None
    stopping: asyncio.Task[bool] | None = None
    settings_runner: web.AppRunner | None = None

    def request_stop(sig: signal.Signals) -> None:
        _LOGGER.info("%s received; stopping Modbus service", sig.name)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_stop, sig)
    try:
        _LOGGER.info("Starting service API on port %s", config.port)
        await site.start()
        _LOGGER.info("Service API ready on port %s", config.port)
        if supervisor_token:
            settings_runner = web.AppRunner(
                create_settings_app(AppSettings(
                    supervisor_token, config.token,
                    overview=lambda: service_overview(service),
                    modbus_trace=service.client.modbus_trace if service is not None else None,
                    configuration_guard=(service.client.async_connection_configuration if service is not None else None),
                )), access_log=None,
            )
            await settings_runner.setup()
            await web.TCPSite(settings_runner, config.listen, INGRESS_PORT).start()
            _LOGGER.info("App connection settings ready through Home Assistant ingress")
        if service is None:
            _LOGGER.info("Connection setup required; open the app Web UI, save and restart the app")
            await stop.wait()
            return
        worker = service.start()
        if supervisor_token and isinstance(config, ServerConfig):
            discovery = asyncio.create_task(publish_discovery(config, supervisor_token))
        stopping = asyncio.create_task(stop.wait())
        done, _pending = await asyncio.wait(
            (stopping, worker), return_when=asyncio.FIRST_COMPLETED
        )
        if worker in done:
            _LOGGER.error("Modbus worker stopped unexpectedly")
            await worker
            raise RuntimeError("Modbus worker stopped")
    finally:
        try:
            for task in (discovery, stopping):
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        finally:
            try:
                try:
                    if settings_runner is not None:
                        await settings_runner.cleanup()
                finally:
                    await runner.cleanup()
            finally:
                if service is not None:
                    await service.stop()
                _LOGGER.info("Modbus service shutdown complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--state", type=Path, default=Path("/data/intent.json"))
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    _LOGGER.info("Starting Mennekes Amtron Modbus service")
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    phase = "reading options"
    try:
        values = json.loads(args.config.read_text())
        if not isinstance(values, dict):
            raise ValueError("configuration must be an object")
        if supervisor_token:
            phase = "loading or generating private app credentials"
            values["token"] = app_token(args.state.with_name("service-token"))
        phase = "validating options"
        config = app_configuration(values) if supervisor_token else ServerConfig.from_dict(values)
    except (ValueError, OSError) as err:
        detail = str(err) if phase == "validating options" else type(err).__name__
        parser.exit(
            2,
            f"Invalid or unreadable service configuration during {phase}: {detail}.\n",
        )
    connection = config.connection if isinstance(config, ServerConfig) else None
    if isinstance(config, ServerConfig) and isinstance(connection, SerialConfig):
        _LOGGER.info(
            "Configuration accepted: mode=%s, transport=serial, Modbus address=%s, "
            "baudrate=%s, frame=%s%s%s",
            "read-only" if config.read_only else "master",
            connection.device_id,
            connection.baudrate,
            connection.bytesize,
            connection.parity,
            connection.stopbits,
        )
    elif isinstance(config, ServerConfig) and isinstance(connection, NetworkConfig):
        _LOGGER.info(
            "Configuration accepted: mode=%s, transport=%s, Modbus address=%s, TCP port=%s",
            "read-only" if config.read_only else "master",
            connection.connection_type,
            connection.device_id,
            connection.tcp_port,
        )
    try:
        asyncio.run(run(config, args.state, supervisor_token))
    except Exception as err:  # noqa: BLE001 - sanitize fatal errors at the process boundary
        # Exception messages can include private paths or credentials. Report
        # the failure class and errno; lifecycle messages identify the phase.
        _LOGGER.error(
            "Modbus service terminated (%s, errno=%s)",
            type(err).__name__,
            getattr(err, "errno", None),
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
