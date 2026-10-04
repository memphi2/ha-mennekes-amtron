"""Minimal Home Assistant stand-ins for the integration tests.

These are deliberately small: the real `DataUpdateCoordinator`, the real
entity classes and the real config-flow helpers are exercised, only the
Home Assistant runtime around them is faked.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.config_entries import ConfigEntryState

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.const import (
    CONF_CONTROL_MODE,
    CONF_PORT,
    CONF_SERIAL_NUMBER,
    CONTROL_MODE_MASTER,
)
from custom_components.mennekes_amtron.control import AmtronControl, ControlOptions
from custom_components.mennekes_amtron.coordinator import MennekesAmtronCoordinator
from custom_components.mennekes_amtron.data import (
    ConnectionState,
    DeviceIdentity,
    MennekesAmtronRuntimeData,
    WriteDiagnostics,
)
from tests.fakes import FakeModbusClient, device_bank


class FakeConfigEntries:
    """The subset of ``hass.config_entries`` the integration uses."""

    def __init__(self) -> None:
        self.entries: dict[str, Any] = {}
        self.forwarded: list[tuple[str, list[Any]]] = []
        self.unloaded: list[tuple[str, list[Any]]] = []
        self.reloaded: list[str] = []
        self.unload_result = True

    def async_get_entry(self, entry_id: str) -> Any:
        """Return a registered entry."""

        return self.entries.get(entry_id)

    async def async_forward_entry_setups(
        self, entry: Any, platforms: Iterable[Any]
    ) -> None:
        """Record a platform forward."""

        self.forwarded.append((entry.entry_id, list(platforms)))

    async def async_unload_platforms(
        self, entry: Any, platforms: Iterable[Any]
    ) -> bool:
        """Record a platform unload."""

        self.unloaded.append((entry.entry_id, list(platforms)))
        return self.unload_result

    async def async_reload(self, entry_id: str) -> None:
        """Record a reload."""

        self.reloaded.append(entry_id)


class FakeServices:
    """The subset of ``hass.services`` the integration uses."""

    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], Any] = {}

    def has_service(self, domain: str, service: str) -> bool:
        """Return whether a service is registered."""

        return (domain, service) in self.registered

    def async_register(
        self, domain: str, service: str, handler: Any, schema: Any = None
    ) -> None:
        """Register a service handler."""

        self.registered[(domain, service)] = (handler, schema)


class FakeHass:
    """Everything the integration touches on ``hass``."""

    def __init__(self, config_dir: str | None = None) -> None:
        self.config_dir = config_dir or tempfile.mkdtemp(prefix="mennekes-hass-")
        self.config = SimpleNamespace(
            config_dir=self.config_dir,
            path=lambda *parts: str(Path(self.config_dir, *parts)),
        )
        self.data: dict[str, Any] = {}
        self.config_entries = FakeConfigEntries()
        self.services = FakeServices()
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()

    def async_create_task(self, target: Any, name: str | None = None) -> Any:
        """Create a task the way Home Assistant does."""

        return asyncio.get_running_loop().create_task(target, name=name)

    async def async_add_executor_job(
        self, target: Callable[..., Any], *args: Any
    ) -> Any:
        """Run a blocking call inline; the fakes do not block."""

        return target(*args)


class FakeConfigEntry:
    """A config entry with the lifecycle hooks the integration uses."""

    def __init__(
        self,
        *,
        entry_id: str = "entry-1",
        title: str = "MENNEKES AMTRON",
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.title = title
        self.domain = "mennekes_amtron"
        self.version = 1
        self.pref_disable_polling = False
        self.state = ConfigEntryState.SETUP_IN_PROGRESS
        self.data = (
            {CONF_PORT: "/dev/fake", CONF_SERIAL_NUMBER: "ABC123456789"}
            if data is None
            else data
        )
        self.options = {} if options is None else options
        self.runtime_data: Any = None
        self.unloads: list[Callable[[], Any]] = []
        self.update_listeners: list[Any] = []
        self.background_tasks: list[asyncio.Task[Any]] = []

    def async_on_unload(self, func: Callable[[], Any]) -> None:
        """Register an unload callback."""

        self.unloads.append(func)

    def add_update_listener(self, listener: Any) -> Callable[[], None]:
        """Register an options update listener."""

        self.update_listeners.append(listener)
        return lambda: None

    def async_create_background_task(
        self, hass: Any, target: Any, name: str
    ) -> asyncio.Task[Any]:
        """Create a background task the way Home Assistant does."""

        task = asyncio.get_running_loop().create_task(target, name=name)
        self.background_tasks.append(task)
        return task


async def build_runtime(
    *,
    hass: FakeHass | None = None,
    entry: FakeConfigEntry | None = None,
    bank: dict[int, int] | None = None,
    read_only: bool = False,
    layout: int = R.LAYOUT_V01_03,
    phase_options: int | None = 2,
) -> tuple[FakeHass, FakeConfigEntry, FakeModbusClient]:
    """Return a loaded entry whose runtime data is wired to a fake bus."""

    hass = hass or FakeHass()
    entry = entry or FakeConfigEntry(
        options={} if read_only else {CONF_CONTROL_MODE: CONTROL_MODE_MASTER}
    )
    transport = FakeModbusClient(bank if bank is not None else device_bank(layout=layout))
    client = MennekesModbusClient(
        SerialConfig(port="/dev/fake", device_id=50),
        client_factory=lambda _config: transport,
    )
    await client.async_connect()

    identity = DeviceIdentity(
        layout_version=layout,
        firmware_version="2023.21.11024",
        serial_number="ABC123456789",
        article_number="1313201205",
        phase_options_hw=phase_options,
        max_evse_current=16.0,
    )
    diagnostics = WriteDiagnostics()
    coordinator = MennekesAmtronCoordinator(
        hass,
        entry,
        client=client,
        identity=identity,
        connection_state=ConnectionState(),
        scan_interval=5,
    )
    control = AmtronControl(
        client,
        diagnostics=diagnostics,
        identity=identity,
        options=ControlOptions(read_only=read_only),
        state=coordinator.current_data,
    )
    entry.runtime_data = MennekesAmtronRuntimeData(
        client=client,
        coordinator=coordinator,
        control=control,
        identity=identity,
        diagnostics=diagnostics,
        connection_state=ConnectionState(),
    )
    hass.config_entries.entries[entry.entry_id] = entry
    await coordinator.async_refresh()
    return hass, entry, transport


def collect(entities: Iterable[Any]) -> dict[str, Any]:
    """Return added entities keyed by their entity-description key."""

    result: dict[str, Any] = {}
    for entity in entities:
        description = getattr(entity, "entity_description", None)
        key = getattr(description, "key", None) or getattr(
            entity, "_attr_translation_key", ""
        )
        result[str(key)] = entity
    return result
