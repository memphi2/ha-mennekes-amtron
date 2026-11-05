from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.runtime_events import EventType
from custom_components.mennekes_amtron_rtu.diagnostics import (
    async_get_config_entry_diagnostics,
    decoded_values,
    fnv1a64,
)
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
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
        entry.runtime_data.diagnostics.timeline.record(EventType.CONNECTION_LOST)
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert dump["runtime_events"][0]["event"] == "connection_lost"
        assert dump["runtime_events"][0]["register_address"] is None
        text = repr(dump)
        assert "/dev/fake" not in text
        assert "ABC123456789" not in text
        assert dump["bus"]["port_category"] == "other"
        assert dump["bus"]["device_id"] == 50
        assert dump["bus"]["response_timeout_seconds"] == 1.0
        assert dump["bus"]["write_retries"] == 0
        assert dump["write_transactions"] == []
        assert dump["device"]["layout_version"] == "v01.03"
        assert dump["device"]["serial_number_hash"]
        assert dump["control"]["read_only"] is False
        assert dump["control"]["heartbeat_running"] is False
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
        hass, entry, transport = await build_runtime()
        entry.runtime_data.diagnostics.last_error = "failed on /dev/fake"
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert "serial_number" not in dump["registers"]
        assert "/dev/fake" not in repr(dump)
        transport.close()
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("connection_type", ["rtu_over_tcp", "modbus_tcp"])
@pytest.mark.parametrize("host", ["wallbox.private.lan", "192.168.15.17", "fd00::1234"])
def test_network_diagnostics_report_protocol_without_adapter_address(connection_type, host):
    async def run():
        hass, entry, _transport = await build_runtime()
        client = entry.runtime_data.client
        config = NetworkConfig(host, connection_type=connection_type)
        client._config = config
        entry.runtime_data.diagnostics.last_error = (
            f"cannot open {config.port}: cannot connect to {host}"
        )
        client.async_read_words = AsyncMock(
            side_effect=AmtronConnectionError(f"connection to {host} lost")
        )
        try:
            dump = await async_get_config_entry_diagnostics(hass, entry)
            assert dump["bus"] == {
                "transport": connection_type,
                "connected": True,
                "device_id": 50,
                "response_timeout_seconds": 1.0,
                "read_retries": 2,
                "write_retries": 0,
            }
            assert host not in repr(dump)
            assert config.port not in repr(dump)
            assert "**REDACTED**" in dump["write_diagnostics"]["last_error"]
            assert "**REDACTED**" in dump["registers"]["status"]["error"]
        finally:
            await client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["unavailable", "invalid", "valid"])
def test_remote_write_diagnostics_are_optional_and_allowlisted(monkeypatch, failure):
    async def run():
        hass, entry, _transport = await build_runtime()
        original = entry.runtime_data.client
        remote = RemoteClient(None, "http://service.invalid:8765", "private-token")
        remote.async_read_words = original.async_read_words
        if failure == "unavailable":
            remote.request = AsyncMock(side_effect=AmtronConnectionError("private-token"))
        else:
            rows = [{"secret": "private-token"}] if failure == "invalid" else []
            events = [{"event": "private-token"}] if failure == "invalid" else [{
                "seconds_since_load": 5.0,
                "event": "heartbeat_failed",
                "register_address": None,
            }]
            remote.request = AsyncMock(return_value={
                "write_transactions": rows, "runtime_events": events, "secret": "private-token",
            })
        entry.runtime_data.client = remote
        dump = await async_get_config_entry_diagnostics(hass, entry)
        assert dump["write_transactions"] == ([] if failure == "valid" else None)
        if failure == "valid":
            assert dump["runtime_events"] == events
        else:
            assert dump["runtime_events"] is None
        assert "private-token" not in repr(dump)
        remote.request.assert_awaited_once_with("GET", "/v1/diagnostics", update_connection=False)
        await original.async_close()

    asyncio.run(run())
