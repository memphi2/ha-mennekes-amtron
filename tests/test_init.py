from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
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
    embedded,
    repairs,
)
from custom_components.mennekes_amtron_rtu.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    SERVICE_SET_CHARGING_CURRENT,
)
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronIdentityError,
)
from custom_components.mennekes_amtron_rtu.device.connection import (
    NetworkConfig,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.embedded import embedded_config
from custom_components.mennekes_amtron_rtu.entry_connection import (
    connection_config,
    serial_config,
)
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass, build_runtime, heartbeat_running


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
        assert heartbeat_running(entry.runtime_data.heartbeat)
        assert hass.config_entries.forwarded[0][1] == _entry_platforms(entry)
        assert entry.update_listeners

        assert await async_unload_entry(hass, entry)
        assert not heartbeat_running(entry.runtime_data.heartbeat)
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
        assert not heartbeat_running(entry.runtime_data.heartbeat)
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
        applied: list[bool] = []
        entry = FakeConfigEntry(options={"charging_network_tariff": {"module": "module_2"}})
        entry.runtime_data = SimpleNamespace(
            client=SimpleNamespace(snapshot={}), plug_charge=None,
            charging_cost=SimpleNamespace(apply_tariff_update=lambda: applied.append(True) or True),
            entry_data=dict(entry.data), vehicle_base_options={},
            vehicle_selection={
                "active_vehicle_profile": None, "vehicle_profile_selected_at": None,
                "vehicle_auto_selection": None,
            },
        )
        await async_reload_entry(hass, entry)
        assert hass.config_entries.reloaded == []
        assert applied == [True]

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
            assert heartbeat_running(entry.runtime_data.heartbeat)
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
        assert not heartbeat_running(entry.runtime_data.heartbeat)
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


def test_another_wallbox_on_the_connection_is_named_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(data=_direct_data("serial"))

        async def acquire(*_args: object) -> None:
            raise AmtronIdentityError("serial number differs")

        monkeypatch.setattr(integration, "async_acquire", acquire)
        with pytest.raises(ConfigEntryNotReady) as caught:
            await async_setup_entry(hass, entry)
        assert caught.value.translation_key == "device_identity_changed"

    asyncio.run(run())


def test_the_embedded_service_pushes_its_polls_and_the_timer_only_backs_them_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(data=_direct_data("serial"))
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        assert await async_setup_entry(hass, entry)
        coordinator = entry.runtime_data.coordinator
        service = _host(hass, entry)._service
        assert coordinator.update_interval.total_seconds() == 2 * service.config.scan_interval
        refreshes = []
        gate = asyncio.Event()

        async def refresh() -> None:
            refreshes.append(1)
            await gate.wait()

        monkeypatch.setattr(coordinator, "async_refresh", refresh)
        # While the entry is still being set up, a poll fetches nothing.
        service.on_polled()
        await asyncio.sleep(0)
        assert refreshes == []
        entry.state = ConfigEntryState.LOADED
        # A poll that ends while the last one is still being fetched adds none.
        service.on_polled()
        service.on_polled()
        await asyncio.sleep(0)
        assert refreshes == [1]
        gate.set()
        await asyncio.sleep(0)
        service.on_polled()
        await asyncio.sleep(0)
        assert refreshes == [1, 1]
        # Home Assistant runs these when it unloads the entry.
        monkeypatch.undo()
        assert await async_unload_entry(hass, entry)
        for unload in entry.unloads:
            result = unload()
            if asyncio.iscoroutine(result):
                await result
        assert service.on_polled is None

    asyncio.run(run())


def test_an_entry_moved_to_the_app_gives_up_the_bus_and_its_own_saved_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        assert await async_setup_entry(hass, entry)
        saved = embedded.state_directory(hass, entry.entry_id)
        # A reload keeps the service; this one changes where the wallbox is.
        embedded.mark_reloading(hass, entry.entry_id, True)
        assert await async_unload_entry(hass, entry)
        assert _host(hass, entry) is not None
        assert (saved / "intent.json").exists()

        class Offline:
            def __init__(self, *_args: object) -> None:
                pass

            async def call(self, *_args: object) -> None:
                raise ServiceConnectionError("service_unavailable", "app offline")

        monkeypatch.setattr(integration, "HttpTransport", Offline)
        monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: None)
        entry.data = {"server_url": "http://app:8765", "server_token": "token"}
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
        assert _host(hass, entry) is None
        assert transport.close_calls == 1
        # The app did not answer: what the own service saved is kept for a
        # way back, until the app has proven to be the right one.
        assert (saved / "intent.json").exists()
        left = []

        async def leave(_hass: object, entry_id: str) -> None:
            left.append(entry_id)

        async def reached(self: object) -> None:
            raise ServiceConnectionError("service_version_mismatch", "after the connection")

        monkeypatch.setattr(integration, "async_leave", leave)
        monkeypatch.setattr(integration.ServiceClient, "async_connect", reached)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
        assert left == []

        # Once the right app answers, the entry's own saved state goes.
        async def answers(self: object) -> None:
            assert left == []
            self.identity = SimpleNamespace(serial_number=None)

        class ReachedError(Exception):
            pass

        def coordinator(*_args: object, **_kwargs: object) -> None:
            raise ReachedError

        monkeypatch.setattr(integration.ServiceClient, "async_connect", answers)
        monkeypatch.setattr(integration.ServiceClient, "bind_identity", lambda _self, _serial: None)
        async def hand_over(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(integration, "ServiceCoordinator", coordinator)
        monkeypatch.setattr(integration, "_async_hand_over_unplug_rule", hand_over)
        with pytest.raises(ReachedError):
            await async_setup_entry(hass, entry)
        assert left == [entry.entry_id]

    asyncio.run(run())


@pytest.mark.parametrize(("state", "kept"), [
    (ConfigEntryState.LOADED, True),
    (ConfigEntryState.SETUP_RETRY, True),
    # Still running on the service: stopping it would strand every entity.
    (ConfigEntryState.FAILED_UNLOAD, True),
    (ConfigEntryState.SETUP_ERROR, False),
    (ConfigEntryState.NOT_LOADED, False),
])
def test_after_a_reload_the_service_stops_only_when_nothing_uses_it_any_more(
    monkeypatch: pytest.MonkeyPatch, state: ConfigEntryState, kept: bool,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        assert await async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        entry.runtime_data = None

        async def reload(_entry_id: str) -> None:
            entry.state = state

        hass.config_entries.async_reload = reload
        await async_reload_entry(hass, entry)
        assert (_host(hass, entry) is not None) is kept
        assert transport.close_calls == (0 if kept else 1)
        entry.runtime_data = runtime
        await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_failing_cleanup_after_a_failed_setup_still_frees_the_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)

        async def platforms(*_args: object) -> None:
            raise RuntimeError("platform failed")

        async def cleanup(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("observer did not stop")

        hass.config_entries.async_forward_entry_setups = platforms
        monkeypatch.setattr(integration, "_async_stop_runtime", cleanup)
        with pytest.raises(RuntimeError, match="observer did not stop"):
            await async_setup_entry(hass, entry)
        # No service is left that nothing would ever stop.
        assert _host(hass, entry) is None
        assert transport.close_calls == 1

    asyncio.run(run())


def test_an_acknowledged_reset_reloads_only_a_loaded_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        hass = FakeHass()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER})
        _patch_client(monkeypatch, FakeModbusClient(device_bank()))
        _patch_issues(monkeypatch)
        reloads: list[bool] = []

        async def reload(_hass: object, _entry: object, *, force: bool) -> None:
            reloads.append(force)

        monkeypatch.setattr(integration, "async_reload_entry", reload)
        assert await async_setup_entry(hass, entry)
        client = entry.runtime_data.client
        rejected = [True]
        monkeypatch.setattr(integration, "_service_intent_rejected", lambda _client: rejected[0])
        # Still being set up: a reload now would take the service from under it.
        client.on_update()
        await asyncio.sleep(0)
        assert reloads == []
        # The first update of the loaded entry catches up.
        entry.state = ConfigEntryState.LOADED
        client.on_update()
        client.on_update()
        await asyncio.sleep(0)
        assert reloads == [True]
        assert await async_unload_entry(hass, entry)

    asyncio.run(run())


def test_setup_hands_the_unplug_rule_to_the_service_only_when_it_differs(monkeypatch) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        client = entry.runtime_data.client
        state = entry.service.client.write_state.unplug_release
        sent: list[bool] = []
        execute = client.execute

        async def counted(command: str, **values: object) -> None:
            sent.append(values["value"])
            await execute(command, **values)

        monkeypatch.setattr(client, "execute", counted)
        hand_over = integration._async_hand_over_unplug_rule
        # Off at the service and not wanted: nothing to say.
        await hand_over(client, entry, controls=True)
        entry.options = {**entry.options, "release_off_on_unplug": True}
        # An entry that only reads never wants it.
        await hand_over(client, entry, controls=False)
        assert sent == []
        await hand_over(client, entry, controls=True)
        await hand_over(client, entry, controls=True)
        assert sent == [True]
        assert state.enabled
        # A service that rejected its saved state takes no setting yet.
        entry.options = {}
        monkeypatch.setattr(integration, "_service_intent_rejected", lambda _client: True)
        await hand_over(client, entry, controls=True)
        assert sent == [True]
        monkeypatch.setattr(integration, "_service_intent_rejected", lambda _client: False)
        await hand_over(client, entry, controls=True)
        assert sent == [True, False]
        assert not state.enabled
        await client.async_close()
        await entry.service.stop()

    asyncio.run(run())


def test_a_service_that_lost_the_unplug_setting_gets_it_again(monkeypatch, caplog) -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.options = {**entry.options, "release_off_on_unplug": True}
        runtime = entry.runtime_data
        state = entry.service.client.write_state.unplug_release
        integration._keep_unplug_rule_handed_over(hass, entry, controls=True)
        failing = [True]
        hand_over = integration._async_hand_over_unplug_rule

        async def flaky(*args: object, **kwargs: object) -> None:
            if failing[0]:
                raise integration.DeviceError("service_unavailable")
            await hand_over(*args, **kwargs)

        monkeypatch.setattr(integration, "_async_hand_over_unplug_rule", flaky)
        # The service does not have it: every poll compares, one task at a time.
        runtime.coordinator.async_update_listeners()
        runtime.coordinator.async_update_listeners()
        assert len(entry.background_tasks) == 1
        await asyncio.gather(*entry.background_tasks)
        assert "not handed to the service (service_unavailable)" in caplog.text
        assert not state.enabled
        failing[0] = False
        runtime.coordinator.async_update_listeners()
        await asyncio.gather(*entry.background_tasks)
        assert state.enabled
        # Now they agree: nothing more is started, nor for an entry that is not loaded.
        runtime.coordinator.async_update_listeners()
        assert len(entry.background_tasks) == 2
        entry.options = {}
        entry.state = ConfigEntryState.NOT_LOADED
        runtime.coordinator.async_update_listeners()
        assert len(entry.background_tasks) == 2
        await runtime.client.async_close()
        await entry.service.stop()

    asyncio.run(run())
