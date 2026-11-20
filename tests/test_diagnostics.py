from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu import diagnostics
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.register_blocks import REGISTER_BLOCKS
from custom_components.mennekes_amtron_rtu.device.runtime_events import EventType
from custom_components.mennekes_amtron_rtu.diagnostics import (
    _CONTROL_KEYS,
    async_get_config_entry_diagnostics,
    decoded_values,
    fnv1a64,
)
from custom_components.mennekes_amtron_rtu.service_client import (
    CONF_SERVER_URL,
    ServiceClient,
)
from tests.ha_fakes import build_runtime


def test_the_serial_hash_is_stable_and_not_the_serial() -> None:
    digest = fnv1a64("ABC123456789")
    assert digest == fnv1a64("ABC123456789")
    assert digest != fnv1a64("ABC123456788")
    assert "ABC123456789" not in str(digest)
    assert fnv1a64(None) is None
    assert fnv1a64("") is None


def test_decoded_values_hide_identifying_strings() -> None:
    dump = decoded_values(
        {
            "evse_state": 5,
            "serial_number": "ABC123456789",
            "firmware_version": "2023",
            "article_number": "1313201205",
            "not_a_register": 1,
        }
    )
    assert dump == {"0x0100 evse_state": 5}


def test_the_dump_carries_no_port_path_and_no_serial_number() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.service.diagnostics.timeline.record(EventType.CONNECTION_LOST)
        data = entry.runtime_data.coordinator.current_data()
        data.connection_quality = "degraded"
        data.connection_failures, data.connection_retries = 2, 100
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert [event["event"] for event in dump["runtime_events"]][-1] == "connection_lost"
        assert dump["runtime_events"][-1]["register_address"] is None
        text = repr(dump)
        assert "/dev/fake" not in text
        assert "ABC123456789" not in text
        assert dump["bus"] == {
            "connected": True, "transport": "serial", "port_category": "other", "device_id": 50,
            "baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 2,
        }
        assert dump["service"]["available"] is True
        assert dump["connection_quality"] == {
            "state": "degraded", "failed_transactions": 2, "retried_transactions": 100,
        }
        assert dump["write_transactions"] == []
        assert dump["device"]["layout_version"] == "v01.03"
        assert dump["device"]["serial_number_hash"] == fnv1a64("ABC123456789")
        assert set(dump["control"]) == set(_CONTROL_KEYS)
        assert dump["control"]["read_only"] is False
        assert dump["heartbeat"] == {"running": False, "healthy": False}
        assert dump["operation"]["controller"] == "direct"
        assert dump["counters"]["writes_sent"] == 0
        assert dump["plug_charge"] is None
        assert dump["poll"]["last_update_success"] is True
        assert "0x0100 evse_state" in dump["values"]
        assert dump["registers"]["status"]["0x0100"] == 1

    asyncio.run(run())


def test_a_failing_block_is_reported_in_the_raw_image() -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        transport.read_exceptions[0x1000] = 0x04
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert "error" in dump["registers"]["statistics"]

    asyncio.run(run())


def test_diagnostics_do_not_expose_identifiers_as_words_or_errors() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        client = entry.runtime_data.client
        client.async_read_words = AsyncMock(
            side_effect=AmtronConnectionError("failed on /dev/fake for ABC123456789")
        )
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert "serial_number" not in dump["registers"]
        assert dump["registers"]["status"] == {"error": "failed on **REDACTED** for **REDACTED**"}
        assert "/dev/fake" not in repr(dump)
        assert "ABC123456789" not in repr(dump)

    asyncio.run(run())


@pytest.mark.parametrize("connection_type", ["rtu_over_tcp", "modbus_tcp"])
@pytest.mark.parametrize("host", ["wallbox.private.lan", "192.168.15.17", "fd00::1234"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_network_diagnostics_report_protocol_without_adapter_address(
    monkeypatch, connection_type, host, authenticated,
):
    async def run():
        hass, entry, _transport = await build_runtime()
        client = entry.runtime_data.client
        auth_key = "testkey5" if authenticated else ""
        config = NetworkConfig(
            host, connection_type=connection_type,
            tcp_authentication=authenticated, tcp_auth_password=auth_key,
        )
        monkeypatch.setattr(diagnostics, "connection_config", lambda _entry: config)
        client.async_read_words = AsyncMock(
            side_effect=AmtronConnectionError(f"connection to {config.port} lost with {auth_key}")
        )
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert dump["bus"] == {
            "transport": connection_type,
            "tcp_authentication": authenticated,
            "connected": True,
            "device_id": config.device_id,
        }
        assert host not in repr(dump)
        assert config.port not in repr(dump)
        if authenticated:
            assert auth_key not in repr(dump)
        assert "**REDACTED**" in dump["registers"]["status"]["error"]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["unavailable", "invalid", "valid"])
def test_app_write_diagnostics_are_optional_and_allowlisted(failure):
    async def run():
        hass, entry, _transport = await build_runtime()
        entry.data = {CONF_SERVER_URL: "http://service.invalid:8765", "token": "private-token"}
        client = entry.runtime_data.client
        original = client.request
        if failure == "unavailable":
            download = AsyncMock(side_effect=AmtronConnectionError("private-token"))
        else:
            rows = [{"secret": "private-token"}] if failure == "invalid" else []
            events = [{"event": "private-token"}] if failure == "invalid" else [{
                "seconds_since_load": 5.0,
                "event": "heartbeat_failed",
                "register_address": None,
            }]
            download = AsyncMock(return_value={
                "write_transactions": rows, "runtime_events": events, "secret": "private-token",
            })

        async def request(method, path, *args, **kwargs):
            if path == "/v1/diagnostics":
                return await download(method, path, *args, **kwargs)
            return await original(method, path, *args, **kwargs)

        client.request = request
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert dump["bus"] == {"connected": True, "transport": "service"}
        assert dump["write_transactions"] == ([] if failure == "valid" else None)
        if failure == "valid":
            assert dump["runtime_events"] == events
        else:
            assert dump["runtime_events"] is None
        assert "private-token" not in repr(dump)
        download.assert_awaited_once_with("GET", "/v1/diagnostics", update_connection=False)
        assert dump["registers"]["status"]["0x0100"] == 1

    asyncio.run(run())


def test_the_plug_and_charge_gate_is_reported_without_its_session() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        entry.runtime_data.plug_charge = SimpleNamespace(
            status="authorized", blocked_reason=None, decision_details={"rule": "selected_vehicle"},
        )
        entry.runtime_data.client.snapshot["control"]["plug_charge"] = {
            "enabled": True, "authorized": True, "session": "private-session", "since": "2026-11-15T10:00:00+00:00",
        }
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert dump["plug_charge"] == {
            "status": "authorized", "blocked_reason": None, "decision": {"rule": "selected_vehicle"},
            "gate_enabled": True, "gate_authorized": True, "session_active": True,
            "session_since": "2026-11-15T10:00:00+00:00",
        }
        assert "private-session" not in repr(dump)

    asyncio.run(run())


def test_remote_diagnostic_reads_leave_the_connection_state_alone():
    async def run():
        block = REGISTER_BLOCKS[0]
        remote = ServiceClient(transport=SimpleNamespace())
        remote.request = AsyncMock(return_value={"words": [0] * block.count})
        assert await remote.async_read_words(address=block.address, count=block.count) == [0] * block.count
        # A failed diagnostics download must not mark the entry unavailable.
        remote.request.assert_awaited_once_with("POST", "/v1/read", {"block": block.name}, update_connection=False)

    asyncio.run(run())
