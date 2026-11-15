"""A direct entry's service: started once, kept through reloads, stopped on unload."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from custom_components.mennekes_amtron_rtu import embedded, service_transport
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronIdentityError,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_api import ServiceBusyError
from custom_components.mennekes_amtron_rtu.device.waveshare_auth import (
    WaveshareAuthenticationRejectedError,
)
from custom_components.mennekes_amtron_rtu.service_transport import EmbeddedTransport
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_client

MASTER = {CONF_CONTROL_MODE: CONTROL_MODE_MASTER}


def _hosts(hass):
    return hass.data.get(f"{DOMAIN}_embedded", {})


def _setup(monkeypatch, tmp_path, *, options=None, bank=None):
    hass = FakeHass(str(tmp_path))
    entry = FakeConfigEntry(options=dict(MASTER if options is None else options))
    wallbox = FakeModbusClient(device_bank() if bank is None else bank)
    _patch_client(monkeypatch, wallbox)
    return hass, entry, wallbox


def test_a_started_service_is_reused_and_takes_changed_intervals(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        try:
            assert _hosts(hass) == {entry.entry_id: host}
            assert host._service.heartbeat.running
            entry.options = {
                **MASTER, CONF_SCAN_INTERVAL_SECONDS: 30,
                CONF_MEASUREMENT_INTERVAL_SECONDS: 60, CONF_CURRENT_LIMIT: 10,
            }
            again = await embedded.async_acquire(hass, entry)
            # The same connection and heartbeat carry on with the new limits.
            assert again is host
            assert host.config.scan_interval == 30
            assert host.config.measurement_interval == 60
            assert host._service.config.current_cap == 10
            assert host._service.heartbeat.running
            assert wallbox.close_calls == 0
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)
        assert wallbox.close_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["connection", "read_only"])
def test_a_changed_connection_or_permission_starts_a_new_service(monkeypatch, tmp_path, change):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        old = await embedded.async_acquire(hass, entry)
        if change == "connection":
            entry.data = {**entry.data, CONF_PORT: "/dev/other"}
        else:
            entry.options = {}
        new = await embedded.async_acquire(hass, entry)
        try:
            assert new is not old
            assert _hosts(hass) == {entry.entry_id: new}
            assert not old._service.heartbeat.running
            assert new._service.heartbeat.running is (change == "connection")
            assert new.config.read_only is (change == "read_only")
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_settings_the_running_service_refuses_are_applied_by_a_restart(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        old = await embedded.async_acquire(hass, entry)

        async def refuse(_config):
            raise DeviceError("service_unavailable")

        monkeypatch.setattr(old._service, "apply_runtime_settings", refuse)
        entry.options = {**MASTER, CONF_SCAN_INTERVAL_SECONDS: 30}
        new = await embedded.async_acquire(hass, entry)
        try:
            assert new is not old
            assert new.config.scan_interval == 30
            assert not old._service.heartbeat.running
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_failed_start_leaves_nothing_running(monkeypatch, tmp_path):
    async def run():
        wallbox = FakeModbusClient()
        wallbox.connect_result = False
        hass, entry, _ = _setup(monkeypatch, tmp_path)
        _patch_client(monkeypatch, wallbox)
        with pytest.raises(AmtronConnectionError):
            await embedded.async_acquire(hass, entry)
        assert _hosts(hass) == {}
        assert not wallbox.connected

    asyncio.run(run())


def test_another_wallbox_is_rejected_before_any_write(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path, bank=device_bank(serial="OTHER0000000"))
        with pytest.raises(AmtronIdentityError):
            await embedded.async_acquire(hass, entry, "ABC123456789")
        assert wallbox.writes == []
        assert _hosts(hass) == {}

    asyncio.run(run())


def test_a_start_that_never_answers_times_out_and_stops(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        entered = asyncio.Event()

        async def blocked(_client):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(service_runtime, "async_read_identity", blocked)
        monkeypatch.setattr(embedded, "_START_SECONDS", 0.05)
        with pytest.raises(TimeoutError):
            await embedded.async_acquire(hass, entry)
        assert entered.is_set()
        assert _hosts(hass) == {}
        assert wallbox.close_calls == 1

    asyncio.run(run())


def test_a_reload_keeps_the_service_and_every_other_release_stops_it(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        embedded.mark_reloading(hass, entry.entry_id, True)
        await embedded.async_release(hass, entry.entry_id)
        assert _hosts(hass) == {entry.entry_id: host}
        assert host._service.heartbeat.running
        # A failed setup during the reload must not keep it.
        await embedded.async_release(hass, entry.entry_id, force=True)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        embedded.mark_reloading(hass, entry.entry_id, False)

        host = await embedded.async_acquire(hass, entry)
        await embedded.async_release(hass, entry.entry_id)
        assert _hosts(hass) == {}
        assert wallbox.close_calls == 2
        # Releasing an entry without a service is harmless.
        await embedded.async_release(hass, entry.entry_id)

    asyncio.run(run())


def test_forgetting_a_removed_entry_stops_it_and_deletes_what_it_saved(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        directory = embedded.state_directory(hass, entry.entry_id)
        assert (directory / "intent.json").exists()
        embedded.mark_reloading(hass, entry.entry_id, True)
        await embedded.async_forget(hass, entry.entry_id)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        assert not directory.exists()
        assert entry.entry_id not in hass.data[f"{DOMAIN}_reloading"]
        # Forgetting twice, or an entry that never ran, is harmless.
        await embedded.async_forget(hass, "never-loaded")

    asyncio.run(run())


class _Api:
    def __init__(self, reply=None):
        self.reply = reply
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = []

    async def dispatch(self, method, path, payload):
        self.calls.append((method, path, payload))
        self.started.set()
        await self.release.wait()
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def test_an_abandoned_request_keeps_running_until_the_service_drains(monkeypatch):
    async def run():
        api = _Api((200, {"accepted": True}))
        transport = EmbeddedTransport(api)
        monkeypatch.setattr(service_transport, "_TIMEOUT_SECONDS", 0.02)
        with pytest.raises(TimeoutError):
            await transport.call("POST", "/v2/command", {"command": "pause"})
        # The command was not cancelled between its writes.
        assert len(transport._running) == 1
        api.release.set()
        await transport.async_drain()
        assert not transport._running
        # Nothing left to wait for.
        await transport.async_drain()

    asyncio.run(run())


def test_a_failed_abandoned_request_is_not_reported_twice(monkeypatch):
    async def run():
        api = _Api(DeviceError("service_unavailable"))
        transport = EmbeddedTransport(api)
        monkeypatch.setattr(service_transport, "_TIMEOUT_SECONDS", 0.02)
        with pytest.raises(TimeoutError):
            await transport.call("GET", "/v2/state", None)
        api.release.set()
        await transport.async_drain()
        assert not transport._running

    asyncio.run(run())


def test_a_busy_service_answers_like_its_http_api():
    async def run():
        api = _Api(ServiceBusyError())
        api.release.set()
        transport = EmbeddedTransport(api)
        assert await transport.call("POST", "/v2/command", {"command": "pause"}) == (
            503, {"error": "service_unavailable"},
        )

    asyncio.run(run())


def test_requests_and_replies_cross_as_independent_json():
    async def run():
        reply = {"values": {"a": 1}}
        api = _Api((200, reply))
        api.release.set()
        transport = EmbeddedTransport(api)
        payload = {"command": "set_current", "value": 8}
        status, body = await transport.call("POST", "/v2/command", payload)
        assert (status, body) == (200, reply)
        assert body is not reply
        assert api.calls[0][2] == payload
        assert api.calls[0][2] is not payload

    asyncio.run(run())


def test_stopping_home_assistant_stops_every_service(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        assert host._service.heartbeat.running
        # Home Assistant stops without unloading its entries.
        await hass.bus.fire(EVENT_HOMEASSISTANT_STOP)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        assert (tmp_path / ".storage" / f"{DOMAIN}.{entry.entry_id}" / "intent.json").exists()

    asyncio.run(run())


def test_a_rejected_gateway_password_is_told_apart_from_a_lost_wallbox(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        try:
            assert host.credentials_rejected() is False
            host._service.failure = AmtronConnectionError("lost")
            assert host.credentials_rejected() is False
            host._service.failure = WaveshareAuthenticationRejectedError("rejected")
            assert host.credentials_rejected() is True
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())
