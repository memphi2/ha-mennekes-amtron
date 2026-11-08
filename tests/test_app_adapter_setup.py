"""Ingress adapter setup binds hardware, protects secrets and saves coherently."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, ClientTimeout, web

from custom_components.mennekes_amtron_rtu.device import app_adapter_setup as detection
from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.waveshare_gateway import (
    GatewayError,
    GatewayRollbackError,
    GatewaySnapshot,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_app_settings import (
    USER,
    FixtureSettings,
    body,
    invoke,
    prepare,
    request,
)

KEY = "TestKey7"


def snapshot(host="192.0.2.50"):
    return GatewaySnapshot(
        address=host, model="RS485 TO POE ETH (B)", firmware="1.523",
        tcp_port=502, baudrate=57600, frame="8n2", connection_type="rtu_over_tcp",
        tcp_authentication=False, can_configure=True,
        _native=b"private-native-credential-state" * 6,
    )


class AdapterFixture:
    def __init__(self):
        self.changed = False
        self.committed = False
        self.rollback_verified = False
        self.calls = []
        self.commit_error = False
        self.rollback_error = False

    async def configure(self, host, settings, password, *, expected):
        self.calls.append((host, settings, password, expected))
        self.changed = True
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            if self.rollback_error:
                raise GatewayRollbackError
            self.changed = False
            self.rollback_verified = True

    async def commit(self):
        if self.commit_error:
            raise GatewayError("gateway_changed")
        self.committed = True


class SettingsFixture(FixtureSettings):
    def __init__(self):
        super().__init__()
        self.options.update(connection_type="LAN: Modbus RTU (TCP)", host="192.0.2.50", tcp_port=502)
        self.options.pop("serial_port")
        self.post_mode = "success"
        self.posts = 0

    async def _request(self, method, path, payload=None):
        if method == "POST":
            self.posts += 1
            if self.posts == 1:
                if self.post_mode != "not_applied":
                    await super()._request(method, path, payload)
                if self.post_mode == "unknown":
                    self.options["scan_interval"] = 17
                if self.post_mode != "success":
                    raise TimeoutError("private supervisor response details")
                return {}
        if self.posts and self.post_mode == "unreadable" and path == "/addons/self/info":
            raise ui.SettingsError("supervisor_unavailable", 503)
        return await super()._request(method, path, payload)


async def inspect(settings, headers, host, revision):
    req = request("POST", headers=headers, payload={"host": host, "revision": revision})
    try:
        return await settings.guard(req, settings.inspect_adapter)
    except web.HTTPException as error:
        return error


async def managed_request(settings):
    headers, payload = await prepare(settings)
    result = body(await inspect(settings, headers, payload["host"], payload["revision"]))
    payload.update(adapter_token=result["adapter_token"], adapter_baudrate=19200,
                   adapter_frame="8e1", tcp_authentication=True, tcp_auth_password=KEY)
    return headers, payload


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(ui, "serial_adapters", list)
    monkeypatch.setattr(detection, "inspect_gateway", AsyncMock(return_value=snapshot()))
    return SettingsFixture()


def test_read_exposes_only_password_presence_and_reuses_it_only_for_same_endpoint(settings):
    settings.options.update(tcp_authentication=True, tcp_auth_password=KEY)

    async def run():
        result = await invoke(settings, request())
        assert KEY not in result.text
        assert body(result)["settings"]["tcp_auth_password_set"] is True
        headers, payload = await prepare(settings)
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 200
        assert settings.options["tcp_auth_password"] == KEY
        headers, payload = await prepare(settings)
        payload["host"] = "192.0.2.51"
        payload["tcp_authentication"] = True
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 400
        assert body(response)["error"] == "connection_invalid"
        assert KEY not in response.text

    asyncio.run(run())


@pytest.mark.parametrize(("saved_host", "submitted_host", "canonical_host"), [
    ("Box.Example.", "Box.Example.", "box.example"),
    ("[2001:DB8::1]", "2001:db8::1", "2001:db8::1"),
    ("täst.example", "xn--tst-qla.example", "xn--tst-qla.example"),
])
def test_saved_password_survives_equivalent_host_notation(
    settings, saved_host, submitted_host, canonical_host
):
    settings.options.update(host=saved_host, tcp_authentication=True, tcp_auth_password=KEY)

    async def run():
        headers, payload = await prepare(settings)
        payload.update(host=submitted_host, tcp_authentication=True, tcp_auth_password="")
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 200
        assert settings.options["host"] == canonical_host
        assert settings.options["tcp_auth_password"] == KEY

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_unconfirmed_ordinary_save_blocks_later_saves_but_allows_inspection(
    settings, monkeypatch, failure
):
    async def run():
        original_request = settings._request
        writes = 0

        async def interrupted(method, path, payload=None):
            nonlocal writes
            if method == "POST":
                writes += 1
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise TimeoutError
            return await original_request(method, path, payload)

        monkeypatch.setattr(settings, "_request", interrupted)
        headers, payload = await prepare(settings)
        payload["device_id"] = 49
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await invoke(settings, request("POST", headers=headers, payload=payload))
        else:
            response = await invoke(settings, request("POST", headers=headers, payload=payload))
            assert body(response) == {"error": "options_save_uncertain"}

        state = body(await invoke(settings, request()))
        assert state["write_blocked"] is True
        headers, managed = await managed_request(settings)
        managed["tcp_port"] = 1502
        response = await invoke(settings, request("POST", headers=headers, payload=managed))
        assert response.status == 503
        assert body(response) == {"error": "options_save_uncertain"}
        assert writes == 1  # no later Supervisor POST, even for an adapter apply

    asyncio.run(run())


def test_confirmed_ordinary_save_after_lost_reply_allows_another_save(settings):
    settings.post_mode = "lost_reply"

    async def run():
        headers, payload = await prepare(settings)
        payload["device_id"] = 49
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 200
        assert body(await invoke(settings, request()))["write_blocked"] is False
        headers, payload = await prepare(settings)
        payload["device_id"] = 48
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 200

    asyncio.run(run())


@pytest.mark.parametrize("managed", [False, True])
def test_unchanged_options_do_not_post_or_overwrite_a_later_save(settings, monkeypatch, managed):
    async def run():
        settings.options["tcp_authentication"] = False
        original = deepcopy(settings.options)
        original_request = settings._request
        delayed = None
        writes = []
        adapter = AdapterFixture()
        monkeypatch.setattr(ui, "configure_gateway", adapter.configure)

        async def simulate_delayed_noop(method, path, payload=None):
            nonlocal delayed
            if method == "POST":
                writes.append(deepcopy(payload["options"]))
                if payload["options"] == original:
                    delayed = deepcopy(payload["options"])
                    raise TimeoutError
            return await original_request(method, path, payload)

        monkeypatch.setattr(settings, "_request", simulate_delayed_noop)
        headers, payload = await prepare(settings)
        if managed:
            detected = body(await inspect(settings, headers, payload["host"], payload["revision"]))
            payload.update(
                adapter_token=detected["adapter_token"], adapter_baudrate=57600,
                adapter_frame="8n2", tcp_authentication=False, tcp_auth_password="",
            )
        result = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert result.status == 200
        assert settings.options == original
        if managed:
            assert adapter.committed  # adapter configuration is still applied

        headers, payload = await prepare(settings)
        payload["device_id"] = 49
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 200
        if delayed is not None:
            settings.options = delayed  # the earlier request completes late
        assert settings.options["device_id"] == 49
        assert delayed is None
        assert len(writes) == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["csrf", "user", "admin", "origin"])
def test_adapter_inspection_requires_admin_and_matching_csrf(settings, change):
    async def run():
        headers, payload = await prepare(settings)
        if change == "csrf":
            headers.pop("X-Amtron-CSRF")
        elif change == "user":
            headers["X-Remote-User-Id"] = "b" * 32
        elif change == "admin":
            headers["X-Remote-User-Groups"] = "system-users"
        else:
            headers["Sec-Fetch-Site"] = "cross-site"
        response = await inspect(settings, headers, payload["host"], payload["revision"])
        assert response.status == 403
        detection.inspect_gateway.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["user", "host", "revision", "expired", "new_probe"])
def test_detection_token_is_bound_to_user_host_revision_and_lifetime(settings, monkeypatch, change):
    async def run():
        now = [100.0]
        monkeypatch.setattr(detection, "time", SimpleNamespace(monotonic=lambda: now[0]))
        manager = settings._adapter_setup
        result = await manager.inspect("192.0.2.50", USER, "revision")
        assert "private-native" not in str(result)
        user, host, revision = USER, "192.0.2.50", "revision"
        if change == "user":
            user = "b" * 32
        elif change == "host":
            host = "192.0.2.51"
        elif change == "revision":
            revision = "changed"
        elif change == "expired":
            now[0] += 301
        else:
            await manager.inspect(host, user, revision)
        with pytest.raises(ValueError, match="expired"):
            manager.expected(result["adapter_token"], user, host, revision)

    asyncio.run(run())


def test_probe_cache_is_bounded_and_forget_invalidates_token(settings):
    async def run():
        manager = settings._adapter_setup
        first = await manager.inspect("192.0.2.50", "0" * 32, "revision")
        for i in range(1, 7):
            latest = await manager.inspect("192.0.2.50", f"{i:032x}", "revision")
        assert len(manager._probes) == 4
        with pytest.raises(ValueError, match="expired"):
            manager.expected(first["adapter_token"], "0" * 32, "192.0.2.50", "revision")
        manager.forget(latest["adapter_token"])
        with pytest.raises(ValueError, match="expired"):
            manager.expected(latest["adapter_token"], f"{6:032x}", "192.0.2.50", "revision")

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["success", "lost_reply", "not_applied", "unknown", "unreadable", "commit_failure", "rollback_failure"])
def test_managed_save_keeps_supervisor_and_gateway_coherent_or_blocks_old_client(settings, monkeypatch, mode):
    async def run():
        adapter = AdapterFixture()
        adapter.commit_error = mode == "commit_failure"
        adapter.rollback_error = mode == "rollback_failure"
        monkeypatch.setattr(ui, "configure_gateway", adapter.configure)
        settings.post_mode = "not_applied" if mode == "rollback_failure" else mode if mode in ("lost_reply", "not_applied", "unknown", "unreadable") else "success"
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(NetworkConfig("192.0.2.50"), client_factory=lambda _: transport)
        settings._configuration_guard = client.async_connection_configuration
        await client.async_connect()
        original = deepcopy(settings.options)
        headers, payload = await managed_request(settings)
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert KEY not in response.text
        assert "private supervisor" not in response.text
        if mode in ("success", "lost_reply"):
            assert response.status == 200
            assert adapter.committed
            assert settings.options["tcp_auth_password"] == KEY
        else:
            assert response.status >= 400
            assert not adapter.committed
        if mode in ("not_applied", "commit_failure"):
            assert adapter.rollback_verified
            assert settings.options == original
        if mode == "commit_failure":
            await client.async_connect()
            assert client.connected
        else:
            with pytest.raises(AmtronConnectionError, match="restart"):
                await client.async_connect()
        if mode in ("not_applied", "unknown", "unreadable"):
            assert body(response)["error"] == "adapter_save_uncertain"
        await client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["missing_host", "extra_field", "invalid_host", "unreachable"])
def test_inspection_rejects_invalid_requests_and_reports_native_failure_without_writes(settings, monkeypatch, change):
    async def run():
        headers, payload = await prepare(settings)
        data = {"host": payload["host"], "revision": payload["revision"]}
        expected = "invalid_request"
        if change == "missing_host":
            del data["host"]
        elif change == "extra_field":
            data["tcp_auth_password"] = KEY
        elif change == "invalid_host":
            data["host"] = "http://private-user:private-password@adapter.invalid"
            expected = "connection_invalid"
        else:
            detection.inspect_gateway.side_effect = GatewayError("gateway_unreachable")
            expected = "gateway_unreachable"
        configure = AsyncMock()
        monkeypatch.setattr(ui, "configure_gateway", configure)
        response = await settings.guard(request("POST", headers=headers, payload=data), settings.inspect_adapter)
        assert response.status == 400
        assert body(response) == {"error": expected}
        assert "private-password" not in response.text
        assert KEY not in response.text
        configure.assert_not_awaited()
        assert not settings.posts
        if change != "unreachable":
            detection.inspect_gateway.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize(("changes", "expected"), [
    ({"adapter_token": "incomplete"}, "invalid_request"),
    ({"adapter_baudrate": 57600, "adapter_frame": "8n2"}, "invalid_request"),
    ({"tcp_auth_password": 123}, "connection_invalid"),
    ({"tcp_auth_password": None}, "connection_invalid"),
    ({"tcp_authentication": "false"}, "connection_invalid"),
    ({"tcp_authentication": 1}, "connection_invalid"),
])
def test_malformed_adapter_and_auth_options_never_touch_adapter_or_supervisor(settings, monkeypatch, changes, expected):
    async def run():
        headers, payload = await prepare(settings)
        original = deepcopy(settings.options)
        configure = AsyncMock()
        monkeypatch.setattr(ui, "configure_gateway", configure)
        response = await invoke(settings, request("POST", headers=headers, payload={**payload, **changes}))
        assert response.status == 400
        assert body(response) == {"error": expected}
        assert settings.options == original
        assert not settings.posts
        configure.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("bad_token", [None, 42, [], "x" * 65, "unknown-proof"])
def test_unrecognized_or_invalid_detection_token_cannot_authorize_a_write(settings, monkeypatch, bad_token):
    async def run():
        headers, payload = await managed_request(settings)
        payload["adapter_token"] = bad_token
        configure = AsyncMock()
        monkeypatch.setattr(ui, "configure_gateway", configure)
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 400
        assert body(response) == {"error": "adapter_detection_required"}
        assert KEY not in response.text
        assert not settings.posts
        configure.assert_not_awaited()
    asyncio.run(run())


def test_save_refuses_a_configuration_that_still_requires_setup(settings, monkeypatch):
    async def run():
        headers, payload = await prepare(settings)
        original = deepcopy(settings.options)
        monkeypatch.setattr(ui, "app_configuration", lambda _: ui.SetupConfig("127.0.0.1", 8765, "synthetic-service-token"))
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 400
        assert body(response) == {"error": "connection_invalid"}
        assert not settings.posts
        assert settings.options == original
    asyncio.run(run())


@pytest.mark.parametrize("running_client", [False, True])
def test_prewrite_authentication_failure_preserves_options_and_can_restore_existing_client(settings, monkeypatch, running_client):
    async def run():
        headers, payload = await managed_request(settings)
        original = deepcopy(settings.options)
        client = None
        if running_client:
            transport = FakeModbusClient(device_bank())
            client = MennekesModbusClient(NetworkConfig("192.0.2.50"), client_factory=lambda _: transport)
            settings._configuration_guard = client.async_connection_configuration
            await client.async_connect()
        configure = AsyncMock(side_effect=GatewayError("gateway_authentication_failed"))
        monkeypatch.setattr(ui, "configure_gateway", configure)
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 400
        assert body(response) == {"error": "gateway_authentication_failed"}
        assert settings.options == original
        assert not settings.posts
        if client is not None:
            await client.async_connect()
            assert client.connected
            await client.async_close()
    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["web_port", "password"])
def test_invalid_adapter_settings_do_not_interrupt_the_running_connection(settings, monkeypatch, invalid):
    async def run():
        detected = snapshot()
        if invalid == "password":
            detected = replace(detected, tcp_authentication=True)
        monkeypatch.setattr(detection, "inspect_gateway", AsyncMock(return_value=detected))
        headers, payload = await managed_request(settings)
        if invalid == "web_port":
            payload["tcp_port"] = int.from_bytes(detected._native[0x62:0x64], "big")
        else:
            payload.update(tcp_authentication=False, tcp_auth_password=f"{KEY} invalid")
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(NetworkConfig("192.0.2.50"), client_factory=lambda _: transport)
        settings._configuration_guard = client.async_connection_configuration
        await client.async_connect()
        configure = AsyncMock()
        monkeypatch.setattr(ui, "configure_gateway", configure)
        try:
            response = await invoke(settings, request("POST", headers=headers, payload=payload))
            assert response.status == 400
            assert body(response) == {"error": "connection_invalid"}
            assert client.connected
            assert not settings.posts
            configure.assert_not_awaited()
        finally:
            await client.async_close()
    asyncio.run(run())


@pytest.mark.parametrize(("option", "external_value"), [("read_only", True), ("current_cap", 12)])
def test_external_option_change_during_adapter_apply_is_not_overwritten(settings, monkeypatch, option, external_value):
    async def run():
        settings.options["read_only"] = False
        adapter = AdapterFixture()

        async def configure_and_change_options(*args, **kwargs):
            receipt = await adapter.configure(*args, **kwargs)
            # The native Supervisor configuration tab is outside the ingress
            # lock and may save while the adapter is applying and rebooting.
            settings.options[option] = external_value
            return receipt

        monkeypatch.setattr(ui, "configure_gateway", configure_and_change_options)
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(NetworkConfig("192.0.2.50"), client_factory=lambda _: transport)
        settings._configuration_guard = client.async_connection_configuration
        await client.async_connect()
        headers, payload = await managed_request(settings)
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 409
        assert body(response) == {"error": "settings_changed"}
        assert settings.options[option] == external_value
        assert not settings.posts
        assert adapter.rollback_verified
        assert not adapter.committed
        await client.async_connect()
        assert client.connected
        await client.async_close()

    asyncio.run(run())


def test_uncertain_save_remains_uncertain_even_if_later_readback_shows_original_options(settings, monkeypatch):
    async def run():
        adapter = AdapterFixture()
        monkeypatch.setattr(ui, "configure_gateway", adapter.configure)
        settings.post_mode = "not_applied"
        original = deepcopy(settings.options)
        real_request = settings._request
        readback_failed = False

        async def temporary_readback_failure(method, path, payload=None):
            nonlocal readback_failed
            if settings.posts and path == "/addons/self/info" and not readback_failed:
                readback_failed = True
                raise ui.SettingsError("supervisor_unavailable", 503)
            return await real_request(method, path, payload)

        monkeypatch.setattr(settings, "_request", temporary_readback_failure)
        headers, payload = await managed_request(settings)
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 503
        assert body(response) == {"error": "adapter_save_uncertain"}
        assert readback_failed
        assert settings.options == original
        assert adapter.rollback_verified
        assert not adapter.changed
        assert not adapter.committed
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["late_timeout", "late_cancel", "confirmed_readback"])
def test_supervisor_post_may_finish_after_client_timeout_or_cancellation(settings, monkeypatch, mode):
    """A real HTTP server can commit after the client has stopped waiting."""
    async def run():
        original = deepcopy(settings.options)
        state = {"options": deepcopy(original)}
        posted = asyncio.Event()
        allow_completion = asyncio.Event()
        completed = asyncio.Event()

        async def supervisor_info(_request):
            return web.json_response({"result": "ok", "data": {"supervisor": "2026.10.5"}})

        async def app_info(_request):
            return web.json_response({"result": "ok", "data": {"options": state["options"]}})

        async def save_options(request):
            payload = await request.json()
            if mode == "confirmed_readback":
                state["options"] = deepcopy(payload["options"])
            posted.set()
            await allow_completion.wait()
            if mode != "confirmed_readback":
                state["options"] = deepcopy(payload["options"])
            completed.set()
            return web.json_response({"result": "ok", "data": {}})

        supervisor = web.Application()
        supervisor.router.add_get("/info", supervisor_info)
        supervisor.router.add_get("/addons/self/info", app_info)
        supervisor.router.add_post("/addons/self/options", save_options)
        runner = web.AppRunner(supervisor, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        monkeypatch.setattr(ui, "SUPERVISOR_URL", f"http://127.0.0.1:{runner.addresses[0][1]}")
        adapter = AdapterFixture()
        monkeypatch.setattr(ui, "configure_gateway", adapter.configure)
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(NetworkConfig("192.0.2.50"), client_factory=lambda _: transport)
        app = ui.AppSettings("isolated-supervisor-token", "isolated-service-token-0123456789abcdef", configuration_guard=client.async_connection_configuration)
        task = None
        try:
            await client.async_connect()
            async with ClientSession(timeout=ClientTimeout(total=0.1)) as session:
                app._session = session
                headers, payload = await managed_request(app)
                task = asyncio.create_task(invoke(app, request("POST", headers=headers, payload=payload)))
                await asyncio.wait_for(posted.wait(), 1)
                if mode == "late_cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    response = await task
                    if mode == "confirmed_readback":
                        assert response.status == 200
                        assert adapter.committed
                    else:
                        assert response.status == 503
                        assert body(response) == {"error": "adapter_save_uncertain"}
                if mode != "confirmed_readback":
                    assert adapter.rollback_verified
                    assert not adapter.changed
                    assert not adapter.committed
                    assert state["options"] == original
                    assert body(await invoke(app, request()))["write_blocked"] is True
                    new_headers, new_payload = await managed_request(app)
                    new_payload["tcp_port"] = 1502
                    rejected = await invoke(app, request("POST", headers=new_headers, payload=new_payload))
                    assert rejected.status == 503
                    assert body(rejected) == {"error": "options_save_uncertain"}
                    assert len(adapter.calls) == 1
                else:
                    assert body(await invoke(app, request()))["write_blocked"] is False
                with pytest.raises(AmtronConnectionError, match="restart"):
                    await client.async_connect()
                allow_completion.set()
                await asyncio.wait_for(completed.wait(), 1)
                assert state["options"]["tcp_auth_password"] == KEY
                if mode != "confirmed_readback":
                    # The delayed save changes Supervisor after rollback, so
                    # the old connection must stay fenced off even now.
                    with pytest.raises(AmtronConnectionError, match="restart"):
                        await client.async_connect()
        finally:
            allow_completion.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client.async_close()
            await runner.cleanup()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_repeated_cancellation_cannot_interrupt_restoring_saved_options(settings, monkeypatch):
    """A cancelled ingress request must finish both rollbacks before exiting."""
    async def run():
        adapter = AdapterFixture()
        monkeypatch.setattr(ui, "configure_gateway", adapter.configure)
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(NetworkConfig("192.0.2.50"), client_factory=lambda _: transport)
        settings._configuration_guard = client.async_connection_configuration
        await client.async_connect()
        original = deepcopy(settings.options)
        headers, payload = await managed_request(settings)
        real_request = settings._request
        save_waiting = asyncio.Event()
        restore_waiting = asyncio.Event()
        allow_restore = asyncio.Event()

        async def delayed_request(method, path, values=None):
            if method == "POST" and not settings.posts:
                await real_request(method, path, values)
                save_waiting.set()
                await asyncio.Event().wait()
            if path == "/addons/self/info" and settings.posts == 1:
                restore_waiting.set()
                await allow_restore.wait()
            return await real_request(method, path, values)

        monkeypatch.setattr(settings, "_request", delayed_request)
        task = asyncio.create_task(invoke(settings, request("POST", headers=headers, payload=payload)))
        try:
            await asyncio.wait_for(save_waiting.wait(), 1)
            assert settings.options != original
            task.cancel()
            await asyncio.wait_for(restore_waiting.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert adapter.rollback_verified
            allow_restore.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert settings.options == original
            assert not adapter.changed
            assert not adapter.committed
            # Cancellation has no verified client response; fail closed until
            # the administrator reconciles and restarts the app.
            with pytest.raises(AmtronConnectionError, match="restart"):
                await client.async_connect()
            assert not any(t.get_name() == "amtron_adapter_options_restore" for t in asyncio.all_tasks())
        finally:
            allow_restore.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await client.async_close()
    asyncio.run(run())
