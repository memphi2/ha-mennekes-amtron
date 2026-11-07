"""Explicit adapter setup uses a reversible transaction around wallbox checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView

from custom_components.mennekes_amtron_rtu import (
    _connection_config,
    _flow_gateway,
    config_flow,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronBusError,
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.waveshare_auth import (
    WaveshareAuthenticationRejectedError,
)
from custom_components.mennekes_amtron_rtu.device.waveshare_gateway import (
    GatewayError,
    GatewayRollbackError,
    GatewaySnapshot,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeHass, build_runtime
from tests.test_config_flow import IDENTITY, Flow, _suggested
from tests.test_home_assistant_smoke import _boot
from tests.test_network_flow import NETWORK_INPUT

PASSWORD = "Demo123"  # noqa: S105 - synthetic fixture
SETTINGS = {"baudrate": 57600, "frame": "8n2", "tcp_authentication": True, "tcp_auth_password": PASSWORD}
SNAPSHOT = GatewaySnapshot(
    address="192.0.2.10", model="RS485 TO POE ETH (B)", firmware="1.523",
    tcp_port=8899, baudrate=9600, frame="8e1", connection_type="rtu_over_tcp",
    tcp_authentication=False, can_configure=True, _native=bytes(128),
)


class Receipt:
    def __init__(self, events):
        self.events = events
        self.committed = False
        self.rollback_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.events.append("rollback")
            if self.rollback_error:
                raise GatewayRollbackError

    async def commit(self):
        self.events.append("commit")
        self.committed = True


def _mocks(monkeypatch, *, failure=None):
    events = []
    receipt = Receipt(events)
    inspect = AsyncMock(return_value=SNAPSHOT)

    async def configure(*args, **kwargs):
        events.append("configure")
        return receipt

    async def probe(config):
        events.append("probe")
        if failure is not None:
            raise failure
        return IDENTITY

    apply = AsyncMock(side_effect=configure)
    probe_mock = AsyncMock(side_effect=probe)
    monkeypatch.setattr(config_flow, "inspect_gateway", inspect)
    monkeypatch.setattr(_flow_gateway, "configure_gateway", apply)
    monkeypatch.setattr(config_flow, "async_test_connection", probe_mock)
    return inspect, apply, probe_mock, receipt, events


def _http_form(result):
    payload = FlowManagerResourceView(None)._prepare_result_json(result)
    rendered = json.dumps(payload)
    assert PASSWORD not in rendered
    assert isinstance(json.loads(rendered)["data_schema"], list)
    root = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    for path in [root / "strings.json", *(root / "translations").glob("*.json")]:
        config = json.loads(path.read_text())["config"]
        fields = config["step"][result["step_id"]]["data"]
        assert {field["name"] for field in payload["data_schema"]} <= fields.keys()
        for error in result.get("errors", {}).values():
            assert config["error"][error]


@pytest.mark.parametrize("step", ["network_auth", "network_adapter"])
def test_details_without_pending_endpoint_return_to_network_form(step):
    async def run():
        result = await getattr(Flow(FakeHass()), f"async_step_{step}")()
        assert result["step_id"] == "network"
        _http_form(result)
    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, ValueError("bad"), AmtronConnectionError("offline"), AmtronBusError("wrong response"), WaveshareAuthenticationRejectedError("rejected")])
def test_existing_tcp_authentication_only_checks_connection(monkeypatch, failure):
    async def run():
        inspect, apply, probe, _, _ = _mocks(monkeypatch, failure=failure)
        flow = Flow(FakeHass())
        form = await flow.async_step_network({**NETWORK_INPUT, "tcp_authentication": True})
        assert form["step_id"] == "network_auth"
        _http_form(form)
        result = await flow.async_step_network_auth({"tcp_auth_password": PASSWORD})
        inspect.assert_not_awaited()
        apply.assert_not_awaited()
        config = probe.await_args.args[0]
        assert config.tcp_auth_password == PASSWORD
        assert PASSWORD not in repr(config)
        if failure is None:
            assert result["type"] == "create_entry"
            assert result["data"]["tcp_auth_password"] == PASSWORD
            assert "configure_gateway" not in result["data"]
            flow.reconfigure_entry.data = result["data"]
            assert _connection_config(flow.reconfigure_entry) == config
            assert "tcp_auth_password" not in flow._suggested_values("network_reconfigure", None)
            assert "tcp_auth_password" not in flow._suggested_values("network", result["data"])
        else:
            assert result["step_id"] == "network_auth"
            _http_form(result)
    asyncio.run(run())


@pytest.mark.parametrize("password", ["", "1234567890", "with space", "é", None])
def test_invalid_tcp_password_never_opens_a_connection(monkeypatch, password):
    async def run():
        inspect, apply, probe, _, _ = _mocks(monkeypatch)
        flow = Flow(FakeHass())
        await flow.async_step_network({**NETWORK_INPUT, "tcp_authentication": True})
        result = await flow.async_step_network_auth({"tcp_auth_password": password})
        assert result["errors"] == {"tcp_auth_password": "invalid_tcp_auth_password"}
        _http_form(result)
        probe.assert_not_awaited()
        inspect.assert_not_awaited()
        apply.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("unsupported", [False, True])
def test_adapter_inspection_failure_keeps_manual_setup_available(monkeypatch, unsupported):
    async def run():
        inspect, apply, probe, _, _ = _mocks(monkeypatch)
        if unsupported:
            inspect.return_value = replace(SNAPSHOT, can_configure=False)
        else:
            inspect.side_effect = GatewayError()
        flow = Flow(FakeHass())
        result = await flow.async_step_network({**NETWORK_INPUT, "configure_gateway": True})
        assert result["errors"] == {"base": "gateway_unsupported" if unsupported else "gateway_unreachable"}
        assert result["step_id"] == "network"
        _http_form(result)
        apply.assert_not_awaited()
        probe.assert_not_awaited()
        manual = await flow.async_step_network(dict(NETWORK_INPUT))
        assert manual["type"] == "create_entry"
        assert inspect.await_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("reconfigure", [False, "serial", "network", "service"])
def test_adapter_preview_is_read_only_and_serial_migration_preserves_bus_values(monkeypatch, reconfigure):
    async def run():
        inspect, apply, probe, _, _ = _mocks(monkeypatch)
        flow = Flow(FakeHass())
        if reconfigure == "serial":
            flow.reconfigure_entry.data = {"port": "/dev/old", "baudrate": 38400, "frame": "8o1"}
        elif reconfigure == "network":
            flow.reconfigure_entry.data = dict(NETWORK_INPUT)
        elif reconfigure == "service":
            flow.reconfigure_entry.data = {"server_url": "http://service.invalid:8765"}
        method = flow.async_step_network_reconfigure if reconfigure else flow.async_step_network
        result = await method({**NETWORK_INPUT, "configure_gateway": True})
        assert result["step_id"] == "network_adapter"
        _http_form(result)
        inspect.assert_awaited_once_with(NETWORK_INPUT["host"])
        apply.assert_not_awaited()
        probe.assert_not_awaited()
        assert _suggested(result)["baudrate"] == (38400 if reconfigure == "serial" else 9600)
        assert _suggested(result)["frame"] == ("8o1" if reconfigure == "serial" else "8e1")
    asyncio.run(run())


def test_factory_adapter_serial_values_show_actual_settings_and_valid_wallbox_defaults(monkeypatch):
    async def run():
        inspect, apply, probe, _, _ = _mocks(monkeypatch)
        inspect.return_value = replace(SNAPSHOT, baudrate=115200, frame="8n1")
        result = await Flow(FakeHass()).async_step_network({**NETWORK_INPUT, "configure_gateway": True})
        assert _suggested(result)["baudrate"] == 57600
        assert _suggested(result)["frame"] == "8n2"
        assert result["description_placeholders"]["baudrate"] == "115200"
        assert result["description_placeholders"]["frame"] == "8n1"
        _http_form(result)
        apply.assert_not_awaited()
        probe.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("authentication", "password"), [(False, ""), (False, PASSWORD), (True, PASSWORD)])
def test_explicit_adapter_apply_checks_identity_then_commits(monkeypatch, authentication, password):
    async def run():
        _, apply, probe, receipt, events = _mocks(monkeypatch)
        flow = Flow(FakeHass())
        await flow.async_step_network({**NETWORK_INPUT, "configure_gateway": True})
        result = await flow.async_step_network_adapter({**SETTINGS, "tcp_authentication": authentication, "tcp_auth_password": password})
        assert result["type"] == "create_entry"
        assert events == ["configure", "probe", "commit"]
        assert receipt.committed
        assert ("tcp_auth_password" in result["data"]) is authentication
        assert probe.await_args.args[0].tcp_authentication is authentication
        assert apply.await_args.kwargs == {"password": password, "expected": SNAPSHOT}
        settings = apply.await_args.args[1]
        assert settings.baudrate == 57600
        assert settings.frame == "8n2"
        assert settings.tcp_port == NETWORK_INPUT["tcp_port"]
        assert settings.connection_type == NETWORK_INPUT["connection_type"]
    asyncio.run(run())


@pytest.mark.parametrize("values", [
    {"tcp_auth_password": "long password"}, {"baudrate": 56000}, {"tcp_authentication": "yes"},
])
def test_invalid_adapter_settings_are_not_applied(monkeypatch, values):
    async def run():
        _, apply, probe, _, _ = _mocks(monkeypatch)
        flow = Flow(FakeHass())
        await flow.async_step_network({**NETWORK_INPUT, "configure_gateway": True})
        result = await flow.async_step_network_adapter({**SETTINGS, **values})
        assert result["errors"] == {"base": "invalid_gateway_config"}
        _http_form(result)
        apply.assert_not_awaited()
        probe.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("failure", [AmtronConnectionError("offline"), AmtronBusError("bad response"), asyncio.CancelledError()])
def test_adapter_probe_failure_or_cancellation_rolls_back(monkeypatch, failure):
    async def run():
        _, _, _, receipt, events = _mocks(monkeypatch, failure=failure)
        flow = Flow(FakeHass())
        await flow.async_step_network({**NETWORK_INPUT, "configure_gateway": True})
        if isinstance(failure, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await flow.async_step_network_adapter(SETTINGS)
        else:
            result = await flow.async_step_network_adapter(SETTINGS)
            _http_form(result)
            assert result["errors"]["base"] in ("cannot_connect_network", "invalid_network_response")
        assert events == ["configure", "probe", "rollback"]
        assert not receipt.committed
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["duplicate", "rejected_result", "form_result", "changed"])
def test_adapter_rejected_finish_never_commits(monkeypatch, failure):
    async def run():
        _, apply, _, receipt, events = _mocks(monkeypatch)
        flow = Flow(FakeHass())
        await flow.async_step_network({**NETWORK_INPUT, "configure_gateway": True})
        if failure == "duplicate":
            def abort():
                raise AbortFlow("already_configured")
            flow._abort_if_unique_id_configured = abort
            with pytest.raises(AbortFlow):
                await flow.async_step_network_adapter(SETTINGS)
        elif failure == "changed":
            apply.side_effect = GatewayError("gateway_changed")
            result = await flow.async_step_network_adapter(SETTINGS)
            assert result["errors"] == {"base": "gateway_changed"}
            _http_form(result)
            return
        else:
            flow._async_finish = AsyncMock(return_value={"type": "abort" if failure == "rejected_result" else "form", "reason": "already_configured"})
            result = await flow.async_step_network_adapter(SETTINGS)
            assert result["reason"] == "already_configured"
        assert events[-1] == "rollback"
        assert not receipt.committed
    asyncio.run(run())


@pytest.mark.parametrize("failure", [None, "probe", "rollback", "unload"])
def test_reconfigure_quiesces_old_client_and_restores_only_after_verified_rollback(monkeypatch, failure):
    async def run():
        _, apply, _, receipt, events = _mocks(
            monkeypatch, failure=AmtronConnectionError("failed") if failure in ("probe", "rollback") else None,
        )
        receipt.rollback_error = failure == "rollback"
        hass, entry, _ = await build_runtime()
        entry.state = ConfigEntryState.LOADED
        flow = Flow(hass)
        flow.reconfigure_entry = entry

        async def unload(_entry_id, *, _lock=True):
            events.append("unload")
            if failure == "unload":
                return False
            await entry.runtime_data.client.async_close()
            entry.state = ConfigEntryState.NOT_LOADED
            return True

        async def restore(_entry_id, *, _lock=True):
            events.append("restore")
            await entry.runtime_data.client.async_connect()
            entry.state = ConfigEntryState.LOADED
            return True

        hass.config_entries.async_unload = unload
        hass.config_entries.async_setup = restore
        hass.config_entries.async_schedule_reload = lambda _: events.append("reload")
        await flow.async_step_network_reconfigure({**NETWORK_INPUT, "configure_gateway": True})
        result = await flow.async_step_network_adapter(SETTINGS)
        if failure is None:
            assert result["reason"] == "reconfigure_successful"
            assert events == ["unload", "configure", "probe", "commit", "reload"]
        elif failure == "unload":
            assert events == ["unload"]
            apply.assert_not_awaited()
        elif failure == "rollback":
            assert events == ["unload", "configure", "probe", "rollback"]
            assert entry.state is ConfigEntryState.NOT_LOADED
            assert result["errors"] == {"base": "gateway_rollback_failed"}
        else:
            assert events == ["unload", "configure", "probe", "rollback", "restore"]
            assert entry.state is ConfigEntryState.LOADED
        await entry.runtime_data.client.async_close()
    asyncio.run(run())


@pytest.mark.parametrize("concurrent_change", [False, "before_probe", "during_commit"])
def test_real_ha_commit_failure_restores_entry_before_reload_or_stays_stopped(monkeypatch, tmp_path, concurrent_change):
    """No new client may start between HA persistence and adapter commit."""
    from tests.test_soc_goal_ha_lifecycle import _entry

    async def run():
        _, _, probe, receipt, events = _mocks(monkeypatch)
        transport = FakeModbusClient(device_bank())
        with (
            patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport),
            patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", return_value=transport) as new_transport,
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                original = dict(entry.data)

                async def fail_commit():
                    assert entry.data["tcp_auth_password"] == PASSWORD
                    assert entry.state is ConfigEntryState.NOT_LOADED
                    await asyncio.sleep(0)
                    new_transport.assert_not_called()
                    if concurrent_change == "during_commit":
                        hass.config_entries.async_update_entry(entry, data={**entry.data, "host": "changed.invalid"})
                    raise GatewayError("gateway_changed")

                receipt.commit = fail_commit
                manager = hass.config_entries.flow
                result = await manager.async_init(DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id})
                result = await manager.async_configure(result["flow_id"], {"next_step_id": "network_reconfigure"})
                result = await manager.async_configure(result["flow_id"], {**NETWORK_INPUT, "configure_gateway": True})
                if concurrent_change == "before_probe":
                    async def fail_probe(_config):
                        hass.config_entries.async_update_entry(entry, data={**NETWORK_INPUT, "host": "changed.invalid"})
                        raise AmtronConnectionError("probe unavailable")
                    probe.side_effect = fail_probe
                result = await manager.async_configure(result["flow_id"], SETTINGS)
                await hass.async_block_till_done()
                new_transport.assert_not_called()
                assert events[-1] == "rollback"
                if concurrent_change:
                    assert result["errors"] == {"base": "gateway_rollback_failed"}
                    assert entry.data["host"] == "changed.invalid"
                    assert entry.state is ConfigEntryState.NOT_LOADED
                else:
                    assert result["errors"] == {"base": "gateway_changed"}
                    assert dict(entry.data) == original
                    assert entry.state is ConfigEntryState.LOADED
            finally:
                await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("manage", [False, True])
def test_real_ha_config_flow_serializes_optional_steps_and_loads_authenticated_entry(monkeypatch, tmp_path, manage):
    async def run():
        inspect, apply, _, receipt, _ = _mocks(monkeypatch)
        transport = FakeModbusClient(device_bank())
        with patch("custom_components.mennekes_amtron_rtu.device.client.WaveshareModbusTcpClient", return_value=transport):
            hass = await _boot(tmp_path / "ha")
            try:
                manager = hass.config_entries.flow
                result = await manager.async_init(DOMAIN, context={"source": "user"})
                result = await manager.async_configure(result["flow_id"], {"next_step_id": "network"})
                _http_form(result)
                result = await manager.async_configure(result["flow_id"], {
                    **NETWORK_INPUT, "tcp_authentication": True, "configure_gateway": manage,
                })
                _http_form(result)
                result = await manager.async_configure(result["flow_id"], SETTINGS if manage else {"tcp_auth_password": PASSWORD})
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                assert entry.state is ConfigEntryState.LOADED
                assert entry.runtime_data.client.config.tcp_auth_password == PASSWORD
                assert entry.runtime_data.client.config.tcp_authentication
                assert not transport.writes
                assert receipt.committed is manage
                assert inspect.await_count == int(manage)
                assert apply.await_count == int(manage)
            finally:
                await hass.async_stop()
    asyncio.run(run())
