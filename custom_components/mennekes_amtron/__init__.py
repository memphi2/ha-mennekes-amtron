"""The MENNEKES AMTRON integration.

This module is a thin facade: it assembles the runtime data of one config
entry and tears it down again. Every rule about the device lives in the
focused modules next to it.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .client import MennekesModbusClient, SerialConfig
from .client_errors import AmtronBusError
from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_STOPBITS,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PARITY,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_STOPBITS,
    DOMAIN,
)
from .control import AmtronControl, ControlOptions
from .coordinator import MennekesAmtronCoordinator
from .data import (
    ConnectionState,
    MennekesAmtronRuntimeData,
    WallboxData,
    WriteDiagnostics,
)
from .entry_types import MennekesAmtronConfigEntry
from .heartbeat import HeartbeatTask
from .identity import async_read_identity
from .options_flow import control_mode_is_master
from .repairs import async_setup_repairs
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

READ_PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR)
CONTROL_PLATFORMS = (
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BUTTON,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""

    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> bool:
    """Set up one wallbox from a config entry."""

    client = MennekesModbusClient(_serial_config(entry))
    try:
        await client.async_connect()
        identity = await async_read_identity(client)
    except AmtronBusError as err:
        await client.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    diagnostics = WriteDiagnostics()
    connection_state = ConnectionState()
    coordinator = MennekesAmtronCoordinator(
        hass,
        entry,
        client=client,
        identity=identity,
        connection_state=connection_state,
        scan_interval=_scan_interval(entry),
    )
    control = AmtronControl(
        client,
        diagnostics=diagnostics,
        identity=identity,
        options=_control_options(entry),
        state=coordinator.current_data,
        request_refresh=coordinator.async_request_refresh,
    )
    entry.runtime_data = MennekesAmtronRuntimeData(
        client=client,
        coordinator=coordinator,
        control=control,
        identity=identity,
        diagnostics=diagnostics,
        connection_state=connection_state,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.async_close()
        raise

    if not control.read_only:
        entry.runtime_data.heartbeat = _start_heartbeat(
            hass, entry, client, diagnostics
        )

    await hass.config_entries.async_forward_entry_setups(entry, _entry_platforms(entry))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    async_setup_repairs(hass, entry)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> bool:
    """Tear down one wallbox."""

    runtime_data = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _entry_platforms(entry)
    )
    if runtime_data.heartbeat is not None:
        await runtime_data.heartbeat.async_stop()
    await runtime_data.control.async_shutdown()
    await runtime_data.client.async_close()
    return unloaded


async def async_reload_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> None:
    """Reload the entry after its options changed."""

    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> bool:
    """Migrate an older config entry.

    Version 1 is the first published schema, so there is nothing to migrate
    yet. A future version lands here instead of in ``async_setup_entry``.
    """

    return entry.version == 1


def _entry_platforms(entry: MennekesAmtronConfigEntry) -> list[Platform]:
    """Return the platforms this entry exposes.

    A read-only entry gets no command entities at all: the safest control
    surface is one that does not exist.
    """

    platforms = list(READ_PLATFORMS)
    if control_mode_is_master(dict(entry.options)):
        platforms.extend(CONTROL_PLATFORMS)
    return platforms


def _serial_config(entry: MennekesAmtronConfigEntry) -> SerialConfig:
    data = entry.data
    return SerialConfig(
        port=str(data[CONF_PORT]),
        baudrate=int(data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)),
        bytesize=int(data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)),
        parity=str(data.get(CONF_PARITY, DEFAULT_PARITY)),
        stopbits=int(data.get(CONF_STOPBITS, DEFAULT_STOPBITS)),
        device_id=int(data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)),
    )


def _scan_interval(entry: MennekesAmtronConfigEntry) -> float:
    value = entry.options.get(
        CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS
    )
    return float(value)


def _control_options(entry: MennekesAmtronConfigEntry) -> ControlOptions:
    options = dict(entry.options)
    cap = options.get(CONF_CURRENT_LIMIT)
    return ControlOptions(
        read_only=not control_mode_is_master(options),
        current_cap=float(cap) if isinstance(cap, (int, float)) else None,
    )


def _start_heartbeat(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    client: MennekesModbusClient,
    diagnostics: WriteDiagnostics,
) -> HeartbeatTask:
    """Start the heartbeat as an entry-owned background task.

    Home Assistant cancels entry background tasks on unload, which is what
    keeps a stale heartbeat from holding the bus after the entry is gone.
    """

    heartbeat = HeartbeatTask(client, diagnostics)
    task = entry.async_create_background_task(
        hass, heartbeat.async_run(), "mennekes_amtron_heartbeat"
    )
    heartbeat.attach(task)
    return heartbeat


__all__ = ["WallboxData", "async_setup", "async_setup_entry", "async_unload_entry"]
