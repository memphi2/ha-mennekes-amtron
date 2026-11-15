"""Run the wallbox service inside Home Assistant for a direct connection.

A direct entry gets the same runtime the app runs, reached without HTTP. It
is kept through a reload of the entry, so the heartbeat never pauses for a
changed option, and stopped when the entry is unloaded for any other reason.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .device.const import (
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    DOMAIN,
)
from .device.errors import DeviceError
from .device.service_api import ServiceApi
from .device.service_config import ServerConfig
from .device.service_departure_runtime import DepartureModbusService
from .entry_connection import connection_config
from .options_flow import control_mode_is_master
from .service_transport import EmbeddedTransport

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_HOSTS = f"{DOMAIN}_embedded"
_RELOADING = f"{DOMAIN}_reloading"
# Reading the identity over a slow serial line with retries takes a while.
_START_SECONDS = 60
_UNCAPPED_AMPERE = 32.0


def embedded_config(entry: MennekesAmtronConfigEntry) -> ServerConfig:
    """Translate a direct entry into the settings the service runs with."""
    options = entry.options
    cap = options.get(CONF_CURRENT_LIMIT)

    def seconds(key: str, default: float) -> float:
        return float(options.get(key, default))

    return ServerConfig(
        connection_config(entry),
        token="",
        read_only=not control_mode_is_master(dict(options)),
        current_cap=float(cap) if isinstance(cap, (int, float)) else _UNCAPPED_AMPERE,
        scan_interval=seconds(CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS),
        measurement_interval=seconds(
            CONF_MEASUREMENT_INTERVAL_SECONDS, DEFAULT_MEASUREMENT_INTERVAL_SECONDS
        ),
        state_change_interval_seconds=seconds(
            CONF_STATE_CHANGE_INTERVAL_SECONDS, DEFAULT_STATE_CHANGE_INTERVAL_SECONDS
        ),
        phase_switch_grace_seconds=seconds(
            CONF_PHASE_SWITCH_GRACE_SECONDS, DEFAULT_PHASE_SWITCH_GRACE_SECONDS
        ),
    )


def state_directory(hass: HomeAssistant, entry_id: str) -> Path:
    return Path(hass.config.path(".storage", f"{DOMAIN}.{entry_id}"))


class EmbeddedService:
    """One running service and the way to reach it."""

    def __init__(self, config: ServerConfig, directory: Path) -> None:
        self.config = config
        self._service = DepartureModbusService(config, directory / "intent.json")
        self.transport = EmbeddedTransport(ServiceApi(self._service))

    async def async_start(self) -> None:
        """Start and wait for the wallbox; a failed start leaves nothing running."""
        self._service.start()
        try:
            async with asyncio.timeout(_START_SECONDS):
                await self._service.wait_started()
        except BaseException:
            await self.async_stop()
            raise

    def fits(self, config: ServerConfig) -> bool:
        """Whether the new settings can be applied without a new connection."""
        return (
            config.connection == self.config.connection
            and config.read_only == self.config.read_only
        )

    async def async_apply(self, config: ServerConfig) -> None:
        await self._service.apply_runtime_settings(config)
        self.config = replace(
            config, connection=self.config.connection, read_only=self.config.read_only
        )

    async def async_stop(self) -> None:
        try:
            await self._service.stop()
        finally:
            await self.transport.async_drain()


def _hosts(hass: HomeAssistant) -> dict[str, EmbeddedService]:
    hosts: dict[str, EmbeddedService] = hass.data.setdefault(_HOSTS, {})
    return hosts


async def async_acquire(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> EmbeddedService:
    """Return the entry's running service, started or adjusted as needed."""
    config = embedded_config(entry)
    hosts = _hosts(hass)
    host = hosts.pop(entry.entry_id, None)
    if host is not None:
        if host.fits(config):
            try:
                await host.async_apply(config)
            except DeviceError:
                # Not ready for new limits: a fresh start applies them.
                await host.async_stop()
            else:
                hosts[entry.entry_id] = host
                return host
        else:
            await host.async_stop()
    host = EmbeddedService(config, state_directory(hass, entry.entry_id))
    await host.async_start()
    hosts[entry.entry_id] = host
    return host


def mark_reloading(hass: HomeAssistant, entry_id: str, reloading: bool) -> None:
    marks: set[str] = hass.data.setdefault(_RELOADING, set())
    (marks.add if reloading else marks.discard)(entry_id)


async def async_release(hass: HomeAssistant, entry_id: str, *, force: bool = False) -> None:
    """Stop the entry's service unless a reload is about to take it over."""
    if not force and entry_id in hass.data.get(_RELOADING, ()):
        return
    host = _hosts(hass).pop(entry_id, None)
    if host is not None:
        await host.async_stop()


async def async_forget(hass: HomeAssistant, entry_id: str) -> None:
    """Stop the service of a removed entry and delete what it saved."""
    await async_release(hass, entry_id, force=True)
    hass.data.get(_RELOADING, set()).discard(entry_id)
    await hass.async_add_executor_job(
        lambda: shutil.rmtree(state_directory(hass, entry_id), ignore_errors=True)
    )
