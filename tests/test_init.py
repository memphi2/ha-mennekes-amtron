from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import (
    _entry_platforms,
    _scan_interval,
    async_reload_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    repairs,
)
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.connection import (
    NetworkConfig,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from custom_components.mennekes_amtron_rtu.embedded import embedded_config
from custom_components.mennekes_amtron_rtu.entry_connection import (
    connection_config,
    serial_config,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: FakeModbusClient) -> None:
    """Let the service a direct entry runs talk to a simulated wallbox."""

    def factory(config: object, **kwargs) -> MennekesModbusClient:
        return MennekesModbusClient(config, client_factory=lambda _c: transport, **kwargs)

    monkeypatch.setattr(service_runtime, "MennekesModbusClient", factory)


def _patch_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repairs.ir, "async_create_issue", lambda *a, **k: None)
    monkeypatch.setattr(repairs.ir, "async_delete_issue", lambda *a, **k: None)


def _direct_data(connection_type: str) -> dict[str, str | int]:
    if connection_type == "serial":
        return {CONF_PORT: "/dev/fake"}
    return {
        "connection_type": connection_type,
        "host": "gateway.local",
        "tcp_port": 1502,
        "device_id": 50,
    }


def _host(hass: FakeHass, entry: FakeConfigEntry):
    return hass.data.get(f"{DOMAIN}_embedded", {}).get(entry.entry_id)


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
        Platform.EVENT,
    ]
    master = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
    assert Platform.NUMBER in _entry_platforms(master)
    assert Platform.BUTTON in _entry_platforms(master)


def test_entry_data_becomes_the_serial_config() -> None:
    entry = FakeConfigEntry(data={CONF_PORT: "/dev/x", CONF_DEVICE_ID: 11})
    config = serial_config(entry)
    assert config.port == "/dev/x"
    assert config.device_id == 11
    assert config.baudrate == 57600
    assert config.stopbits == 2
    assert connection_config(entry) == config
    assert isinstance(connection_config(entry), SerialConfig)


@pytest.mark.parametrize("protocol", ["rtu_over_tcp", "modbus_tcp"])
def test_network_entry_data_becomes_the_connection_config(protocol):
    config = connection_config(FakeConfigEntry(data=_direct_data(protocol)))
    assert config == NetworkConfig(host="gateway.local", tcp_port=1502, connection_type=protocol)


def test_options_drive_the_interval_and_the_service_settings() -> None:
    entry = FakeConfigEntry()
    assert _scan_interval(entry) == 5.0
    assert embedded_config(entry).read_only is True
    # Without a configured limit the service follows the wallbox's maximum
    # and allows only the minimum until the wallbox has reported it.
    assert embedded_config(entry).current_cap == 6.0
    assert embedded_config(entry).cap_from_wallbox is True

    entry = FakeConfigEntry(
        options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER,
            CONF_SCAN_INTERVAL_SECONDS: 30,
            CONF_CURRENT_LIMIT: 12.5,
        }
    )
    assert _scan_interval(entry) == 30.0
    config = embedded_config(entry)
    assert config.read_only is False
    assert config.current_cap == 12.5
    assert config.cap_from_wallbox is False
    assert config.scan_interval == 30.0

    # With the app, its settings count: HA keeps its own pace.
    entry = FakeConfigEntry(
        data={"server_url": "http://app:8765"},
        options={CONF_SCAN_INTERVAL_SECONDS: 30, CONF_CURRENT_LIMIT: 12.5},
    )
    assert _scan_interval(entry) == 5.0


@pytest.mark.parametrize("connection_type", ["serial", "rtu_over_tcp", "modbus_tcp"])
def test_a_master_entry_loads_and_unloads_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    connection_type: str,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(
            data=_direct_data(connection_type),
            options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER},
        )
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        assert await async_setup_entry(hass, entry)
        assert _host(hass, entry).config.connection.connection_type == connection_type
        assert entry.runtime_data.identity.serial_number == "ABC123456789"
        assert entry.runtime_data.heartbeat is not None
        assert entry.runtime_data.heartbeat.running
        assert hass.config_entries.forwarded[0][1] == _entry_platforms(entry)
        assert entry.update_listeners

        assert await async_unload_entry(hass, entry)
        assert not entry.runtime_data.heartbeat.running
        assert _host(hass, entry) is None
        assert transport.close_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("connection_type", ["serial", "rtu_over_tcp", "modbus_tcp"])
def test_a_read_only_entry_never_starts_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    connection_type: str,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(data=_direct_data(connection_type))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        assert await async_setup_entry(hass, entry)
        assert not entry.runtime_data.heartbeat.running
        assert not _host(hass, entry)._service.heartbeat.running
        assert await async_unload_entry(hass, entry)
        assert transport.writes == []

    asyncio.run(run())


@pytest.mark.parametrize("change", ["enable_master", "disable_master", "service"])
def test_unload_retains_the_platforms_from_before_entry_updates(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(
            options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER}
            if change == "disable_master" else {}
        )
        _patch_client(monkeypatch, FakeModbusClient(device_bank()))
        _patch_issues(monkeypatch)
        assert await async_setup_entry(hass, entry)
        loaded = hass.config_entries.forwarded[-1][1]
        # HA replaces config data/options before calling the update listener.
        if change == "service":
            entry.data = {"server_url": "http://service:8765"}
        else:
            entry.options = (
                {CONF_CONTROL_MODE: CONTROL_MODE_MASTER}
                if change == "enable_master" else {}
            )
        assert loaded != _entry_platforms(entry)
        assert await async_unload_entry(hass, entry)
        assert hass.config_entries.unloaded[-1][1] == loaded

    asyncio.run(run())


@pytest.mark.parametrize("connection_type", ["serial", "rtu_over_tcp", "modbus_tcp"])
def test_a_dead_bus_defers_the_setup(monkeypatch: pytest.MonkeyPatch, connection_type: str) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(data=_direct_data(connection_type))
        transport = FakeModbusClient()
        transport.connect_result = False
        _patch_client(monkeypatch, transport)

        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
        assert _host(hass, entry) is None
        assert transport.close_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("connection_type", ["serial", "rtu_over_tcp", "modbus_tcp"])
def test_a_failing_first_poll_closes_the_port(
    monkeypatch: pytest.MonkeyPatch,
    connection_type: str,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(data=_direct_data(connection_type))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        original = integration.ServiceCoordinator

        class Coordinator(original):  # type: ignore[misc, valid-type]
            async def async_config_entry_first_refresh(self) -> None:
                raise ConfigEntryNotReady("poll failed")

        monkeypatch.setattr(integration, "ServiceCoordinator", Coordinator)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
        assert transport.close_calls == 1
        assert _host(hass, entry) is None

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


def test_tariff_only_option_update_does_not_restart_modbus() -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry()
        entry.runtime_data = SimpleNamespace(
            client=SimpleNamespace(snapshot={}), plug_charge=None,
            charging_cost=SimpleNamespace(apply_tariff_only_update=lambda: True),
            entry_data=dict(entry.data), vehicle_base_options={},
            vehicle_selection={
                "active_vehicle_profile": None, "vehicle_profile_selected_at": None,
                "vehicle_auto_selection": None,
            },
        )
        await async_reload_entry(hass, entry)
        assert hass.config_entries.reloaded == []

    asyncio.run(run())


def test_a_failed_unload_keeps_the_connection_and_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        assert await async_setup_entry(hass, entry)
        try:
            hass.config_entries.unload_result = False
            assert not await async_unload_entry(hass, entry)
            assert entry.runtime_data.client.connected
            assert entry.runtime_data.heartbeat.running
            assert _host(hass, entry) is not None
            assert transport.close_calls == 0
        finally:
            hass.config_entries.unload_result = True
            await async_unload_entry(hass, entry)

    asyncio.run(run())


def test_failed_platform_setup_releases_the_port_and_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        async def fail(*args: object) -> None:
            raise RuntimeError("platform setup failed")

        monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", fail)
        with pytest.raises(RuntimeError, match="platform setup failed"):
            await async_setup_entry(hass, entry)
        assert transport.close_calls == 1
        assert not entry.runtime_data.heartbeat.running
        assert _host(hass, entry) is None

    asyncio.run(run())


def test_cancelled_identity_read_releases_the_serial_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry()
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        entered = asyncio.Event()

        async def blocked_identity(_client: object) -> None:
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(service_runtime, "async_read_identity", blocked_identity)
        setup = asyncio.create_task(async_setup_entry(hass, entry))
        await entered.wait()
        setup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await setup
        assert transport.close_calls == 1
        assert not transport.connected
        assert _host(hass, entry) is None

    asyncio.run(run())


def test_a_failed_cleanup_of_deleted_vehicle_storage_does_not_block_setup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={"vehicle_profiles": {}})
        _patch_client(monkeypatch, FakeModbusClient(device_bank()))
        _patch_issues(monkeypatch)

        async def fail(*_args: object) -> None:
            raise OSError("read-only storage")

        monkeypatch.setattr(integration, "async_prune_profile_losses", fail)
        assert await async_setup_entry(hass, entry)
        assert "deleted vehicle storage not removed" in caplog.text
        assert await async_unload_entry(hass, entry)

    asyncio.run(run())


def test_optional_setup_failures_keep_reauthentication_and_retry_reasons() -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed

    client = SimpleNamespace(failure_reason=None)
    auth = ConfigEntryAuthFailed("credential rejected")
    with pytest.raises(ConfigEntryAuthFailed) as caught:
        integration._raise_optional_setup_error(client, auth, "soc_goal_unavailable")
    assert caught.value is auth
    retry = ConfigEntryNotReady("try later")
    with pytest.raises(ConfigEntryNotReady) as caught:
        integration._raise_optional_setup_error(client, retry, "soc_goal_unavailable")
    assert caught.value is retry
    with pytest.raises(ConfigEntryNotReady) as caught:
        integration._raise_optional_setup_error(client, RuntimeError("x"), "soc_goal_unavailable")
    assert caught.value.translation_key == "soc_goal_unavailable"
    client.failure_reason = "service_auth_failed"
    with pytest.raises(ConfigEntryAuthFailed) as caught:
        integration._raise_optional_setup_error(client, RuntimeError("x"), "soc_goal_unavailable")
    assert caught.value.translation_key == "service_auth_failed"
