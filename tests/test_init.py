from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron as integration
from custom_components.mennekes_amtron import (
    _control_options,
    _entry_platforms,
    _scan_interval,
    _serial_config,
    async_migrate_entry,
    async_reload_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    repairs,
)
from custom_components.mennekes_amtron.client import MennekesModbusClient
from custom_components.mennekes_amtron.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, transport: FakeModbusClient
) -> None:
    def factory(config: object) -> MennekesModbusClient:
        return MennekesModbusClient(config, client_factory=lambda _c: transport)

    monkeypatch.setattr(integration, "MennekesModbusClient", factory)


def _patch_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repairs.ir, "async_create_issue", lambda *a, **k: None)
    monkeypatch.setattr(repairs.ir, "async_delete_issue", lambda *a, **k: None)


def test_setup_registers_the_action() -> None:
    async def run() -> None:
        hass = FakeHass()
        assert await async_setup(hass, {})
        assert hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_CURRENT)

    asyncio.run(run())


def test_a_read_only_entry_exposes_no_control_platforms() -> None:
    assert _entry_platforms(FakeConfigEntry()) == [
        Platform.SENSOR,
        Platform.BINARY_SENSOR,
    ]
    master = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
    assert Platform.NUMBER in _entry_platforms(master)
    assert Platform.BUTTON in _entry_platforms(master)


def test_entry_data_becomes_the_serial_config() -> None:
    entry = FakeConfigEntry(data={CONF_PORT: "/dev/x", CONF_DEVICE_ID: 11})
    config = _serial_config(entry)
    assert config.port == "/dev/x"
    assert config.device_id == 11
    assert config.baudrate == 57600
    assert config.stopbits == 2


def test_options_drive_the_interval_and_the_control_options() -> None:
    entry = FakeConfigEntry()
    assert _scan_interval(entry) == 5.0
    assert _control_options(entry).read_only is True
    assert _control_options(entry).current_cap is None

    entry = FakeConfigEntry(
        options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
            CONF_SCAN_INTERVAL_SECONDS: 30,
            CONF_CURRENT_LIMIT: 12.5,
        }
    )
    assert _scan_interval(entry) == 30.0
    options = _control_options(entry)
    assert options.read_only is False
    assert options.current_cap == 12.5


def test_a_master_entry_loads_and_unloads_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.identity.serial_number == "ABC123456789"
        assert entry.runtime_data.heartbeat is not None
        assert entry.runtime_data.heartbeat.running
        assert hass.config_entries.forwarded[0][1] == _entry_platforms(entry)
        assert entry.update_listeners

        assert await async_unload_entry(hass, entry)
        assert not entry.runtime_data.heartbeat.running
        assert transport.close_calls == 1

    asyncio.run(run())


def test_a_read_only_entry_never_starts_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry()
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.heartbeat is None
        assert await async_unload_entry(hass, entry)
        assert transport.writes == []

    asyncio.run(run())


def test_a_dead_bus_defers_the_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry()
        transport = FakeModbusClient()
        transport.connect_result = False
        _patch_client(monkeypatch, transport)

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    asyncio.run(run())


def test_a_failing_first_poll_closes_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry()
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        async def failing_refresh() -> None:
            raise ConfigEntryNotReady("poll failed")

        original = integration.MennekesAmtronCoordinator

        class Coordinator(original):  # type: ignore[misc, valid-type]
            async def async_config_entry_first_refresh(self) -> None:
                await failing_refresh()

        monkeypatch.setattr(integration, "MennekesAmtronCoordinator", Coordinator)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
        assert transport.close_calls == 1

    asyncio.run(run())


def test_reload_asks_home_assistant_to_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry()
        await async_reload_entry(hass, entry)
        assert hass.config_entries.reloaded == [entry.entry_id]

    asyncio.run(run())


def test_only_the_known_entry_version_is_accepted() -> None:
    async def run() -> None:
        entry = FakeConfigEntry()
        assert await async_migrate_entry(FakeHass(), entry)
        entry.version = 2
        assert not await async_migrate_entry(FakeHass(), entry)

    asyncio.run(run())
