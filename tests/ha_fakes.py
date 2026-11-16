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

from custom_components.mennekes_amtron_rtu.coordinator import ServiceCoordinator
from custom_components.mennekes_amtron_rtu.data import MennekesAmtronRuntimeData
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONF_PORT,
    CONF_SERIAL_NUMBER,
    CONTROL_MODE_MASTER,
)
from custom_components.mennekes_amtron_rtu.device.service_api import ServiceApi
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
    DepartureModbusService,
)
from custom_components.mennekes_amtron_rtu.guest_charging import GuestCharging
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.service_transport import EmbeddedTransport
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

    def async_entries(self, domain: str | None = None) -> list[Any]:
        """Return the registered entries of one integration."""

        return [entry for entry in self.entries.values() if domain in (None, entry.domain)]

    def async_update_entry(
        self, entry: Any, *, title: str | None = None, options: dict[str, Any] | None = None,
    ) -> None:
        """Apply a generated display title or persisted option update."""

        if title is not None:
            entry.title = title
        if options is not None:
            entry.options = options

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
        self, domain: str, service: str, handler: Any, schema: Any = None, supports_response: Any = None, **kwargs: Any
    ) -> None:
        """Register a service handler."""

        self.registered[(domain, service)] = (handler, schema)


class FakeBus:
    """Record one-time listeners and fire them on request."""

    def __init__(self) -> None:
        self.once: dict[str, list[Callable[[Any], Any]]] = {}

    def async_listen_once(self, event_type: str, listener: Callable[[Any], Any]) -> Callable[[], None]:
        self.once.setdefault(event_type, []).append(listener)
        return lambda: self.once[event_type].remove(listener)

    async def fire(self, event_type: str) -> None:
        for listener in self.once.pop(event_type, []):
            await listener(SimpleNamespace(event_type=event_type, data={}))


class FakeHass:
    """Everything the integration touches on ``hass``."""

    def __init__(self, config_dir: str | None = None) -> None:
        self.config_dir = config_dir or tempfile.mkdtemp(prefix="mennekes-hass-")
        self.config = SimpleNamespace(
            config_dir=self.config_dir,
            components=set(),
            language="en",
            path=lambda *parts: str(Path(self.config_dir, *parts)),
        )
        self.data: dict[str, Any] = {}
        self.bus = FakeBus()
        self.is_stopping = False
        self.config_entries = FakeConfigEntries()
        self.services = FakeServices()
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()

    def async_create_task(
        self, target: Any, name: str | None = None, *, eager_start: bool = True,
    ) -> Any:
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
        self.domain = "mennekes_amtron_rtu"
        self.version = 1
        self.pref_disable_polling = False
        self.state = ConfigEntryState.SETUP_IN_PROGRESS
        self.data = (
            {CONF_PORT: "/dev/fake", CONF_SERIAL_NUMBER: "ABC123456789"}
            if data is None
            else data
        )
        self.options = {} if options is None else options
        self.setup_lock = asyncio.Lock()
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
        self, hass: Any, target: Any, name: str, eager_start: bool = True
    ) -> asyncio.Task[Any]:
        """Create a background task the way Home Assistant does."""

        task = asyncio.get_running_loop().create_task(target, name=name)
        self.background_tasks.append(task)
        return task


class PollingTransport(EmbeddedTransport):
    """Reach a service that polls the wallbox whenever its state is read.

    The real service polls on its own clock. A test wants the next refresh to
    show what the simulated wallbox holds now.
    """

    def __init__(self, service: DepartureModbusService) -> None:
        super().__init__(ServiceApi(service))
        self.service = service

    async def call(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        if path.startswith("/v2/state"):
            await self.service.refresh()
        return await super().call(method, path, payload)


async def build_runtime(
    *,
    hass: FakeHass | None = None,
    entry: FakeConfigEntry | None = None,
    bank: dict[int, int] | None = None,
    read_only: bool = False,
    layout: int = R.LAYOUT_V01_03,
    current_cap: float = 32.0,
) -> tuple[FakeHass, FakeConfigEntry, FakeModbusClient]:
    """Return a loaded entry whose service runs on a simulated wallbox.

    ``entry.service`` is the service behind the entry; its heartbeat is
    stopped so that a test sees only the writes it caused.
    """

    hass = hass or FakeHass()
    entry = entry or FakeConfigEntry(
        options={} if read_only else {CONF_CONTROL_MODE: CONTROL_MODE_MASTER}
    )
    wallbox = FakeModbusClient(bank if bank is not None else device_bank(layout=layout))
    modbus = MennekesModbusClient(
        SerialConfig(port="/dev/fake", device_id=50),
        client_factory=lambda _config: wallbox,
    )
    directory = Path(tempfile.mkdtemp(prefix="amtron-service-"))
    service = DepartureModbusService(
        ServerConfig(modbus.config, "", read_only=read_only, current_cap=current_cap),
        directory / "intent.json",
        client=modbus,
    )
    await service._initialize()
    await service.heartbeat.async_stop()
    wallbox.writes.clear()

    client = ServiceClient(transport=PollingTransport(service))
    await client.async_connect()
    coordinator = ServiceCoordinator(hass, entry, client=client, scan_interval=5)

    def notify_pending() -> None:
        coordinator.async_update_listeners()

    control = ServiceControl(
        client,
        state=coordinator.current_data,
        request_refresh=coordinator.async_refresh,
        notify_pending=notify_pending,
    )
    entry.runtime_data = MennekesAmtronRuntimeData(
        client=client,
        coordinator=coordinator,
        control=control,
        guest=GuestCharging(client, coordinator.async_refresh),
        identity=client.identity,
        entry_data=dict(entry.data),
        controls=not read_only,
    )
    entry.service = service
    hass.config_entries.entries[entry.entry_id] = entry
    await coordinator.async_refresh()
    return hass, entry, wallbox


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


def capture_timers(monkeypatch: Any, module: Any) -> list[list[Any]]:
    """Collect ``[seconds, action, waiting]`` for a module's delayed calls.

    A test fires or inspects them instead of waiting for the clock.
    """
    timers: list[list[Any]] = []

    def call_later(_hass: Any, seconds: float, action: Any) -> Callable[[], None]:
        timer = [seconds, action, True]
        timers.append(timer)

        def cancel() -> None:
            timer[2] = False

        return cancel

    monkeypatch.setattr(module, "async_call_later", call_later)
    return timers


def heartbeat_running(heartbeat: Any) -> bool:
    """Whether the service Home Assistant talks to reports a running heartbeat."""
    client = heartbeat._client
    return bool(client.connected and client.snapshot.get("heartbeat", {}).get("running") is True)
