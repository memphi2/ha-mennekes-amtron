"""App ingress must protect credentials and configure only the selected connection."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from aiohttp import ClientConnectionError, web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig

TOKEN = "isolated-test-service-credential-0123456789"  # noqa: S105 - fixture
USER = "a" * 32
HEADERS = {"X-Remote-User-Id": USER, "X-Remote-User-Groups": "system-admin"}
BASE = {
    "connection_type": "USB / RS-485", "serial_port": "/dev/serial/by-id/missing",
    "device_id": 50, "baudrate": "57600", "frame": "8n2", "read_only": True,
    "current_cap": 6, "scan_interval": 5, "measurement_interval": 30,
    "state_change_interval_seconds": 300, "phase_switch_grace_seconds": 5,
}
ADAPTER = ui.SerialAdapter("/dev/serial/by-id/adapter", "Adapter (/dev/ttyUSB0)", "/dev/ttyUSB0")


class FixtureSettings(ui.AppSettings):
    """Model the public Supervisor self API without network or real credentials."""

    def __init__(self):
        super().__init__("isolated-supervisor-token", TOKEN)
        self.options = deepcopy(BASE)
        self.version = "2026.11.0"
        self.calls = []

    async def _request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/info":
            return {"supervisor": self.version}
        if method == "GET":
            return {"options": deepcopy(self.options)}
        await asyncio.sleep(0)
        self.options = deepcopy(payload["options"])
        return {}


def request(method="GET", *, headers=None, payload=None, peer=ui.SUPERVISOR_PEER):
    headers = dict(HEADERS if headers is None else headers)
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")
    transport = Mock()
    transport.get_extra_info.return_value = (peer, 1234)
    req = make_mocked_request(method, "/api/config", headers=headers, transport=transport)
    if payload is not None:
        req._read_bytes = json.dumps(payload).encode()
    return req


async def invoke(settings, req):
    try:
        return await settings.guard(req, settings.save if req.method == "POST" else settings.read)
    except web.HTTPException as error:
        return error


def body(response):
    return json.loads(response.text)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(ui, "serial_adapters", lambda: [ADAPTER])
    return FixtureSettings()


async def prepare(settings, transport="rtu_over_tcp"):
    state = body(await invoke(settings, request()))
    payload = {"connection_type": transport, "device_id": 50, "revision": state["revision"]}
    if transport == "serial":
        payload.update(serial_port=ADAPTER.value, baudrate="57600", frame="8n2")
    else:
        payload.update(host="192.0.2.50", tcp_port=502)
    return {**HEADERS, "X-Amtron-CSRF": state["csrf"]}, payload


@pytest.mark.parametrize("peer", ["127.0.0.1", "172.30.32.3", "::ffff:172.30.32.2", "untrusted"])
def test_only_real_supervisor_peer_is_accepted(settings, peer):
    async def check():
        headers = {**HEADERS, "X-Forwarded-For": ui.SUPERVISOR_PEER, "Forwarded": f"for={ui.SUPERVISOR_PEER}"}
        assert (await invoke(settings, request(headers=headers, peer=peer))).status == 403
        assert settings.calls == []
    asyncio.run(check())


@pytest.mark.parametrize("headers", [
    {}, {**HEADERS, "X-Remote-User-Id": "admin"},
    {**HEADERS, "X-Remote-User-Groups": "not-system-admin"},
    {**HEADERS, "X-Remote-User-Groups": "system-users"},
    {**HEADERS, "Sec-Fetch-Site": "cross-site"},
])
def test_admin_and_same_site_are_required(settings, headers):
    async def check():
        assert (await invoke(settings, request(headers=headers))).status == 403
    asyncio.run(check())


@pytest.mark.parametrize("version", [None, 202611, "2026.10.1", "2026.11.0b1", "2026.11.0.dev0", "2026.13.0", "2027.0.0", "x" * 33])
def test_old_or_unverified_supervisor_versions_fail_closed(settings, version):
    async def check():
        assert (await invoke(settings, request())).status == 200
        settings.version = version
        response = await invoke(settings, request())
        assert response.status == 503
        assert body(response) == {"error": "supervisor_update_required"}
    asyncio.run(check())


def test_public_read_contains_only_connection_fields_and_no_credentials(settings):
    settings.options["token"] = TOKEN
    async def check():
        response = await invoke(settings, request())
        result = body(response)
        assert result["settings"]["connection_type"] == "serial"
        assert result["settings"]["serial_port"] == BASE["serial_port"]
        assert result["adapters"] == [{"value": ADAPTER.value, "label": ADAPTER.label}]
        assert not {"token", "read_only", "current_cap"} & result["settings"].keys()
        assert TOKEN not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    asyncio.run(check())


def test_present_physical_adapter_uses_stable_alias(settings):
    settings.options["serial_port"] = ADAPTER.device
    async def check():
        result = body(await invoke(settings, request()))
        assert result["settings"]["serial_port"] == ADAPTER.value
    asyncio.run(check())


@pytest.mark.parametrize("transport", ["rtu_over_tcp", "modbus_tcp", "serial"])
def test_save_changes_only_connection_and_allows_another_save(settings, transport):
    async def check():
        headers, payload = await prepare(settings, transport)
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 200
        result = body(response)
        assert result["saved"]
        assert result["restart_required"]
        assert settings.options["connection_type"] == ui._TRANSPORT_LABELS[transport]
        if transport == "serial":
            assert settings.options["serial_port"] == ADAPTER.value
        else:
            assert "serial_port" not in settings.options
        for key in ("read_only", "current_cap", "scan_interval", "measurement_interval", "state_change_interval_seconds", "phase_switch_grace_seconds"):
            assert settings.options[key] == BASE[key]
        payload["revision"] = result["revision"]
        payload["device_id"] = 49
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 200
        assert settings.options["device_id"] == 49
        assert all(path in ("/info", "/addons/self/info", "/addons/self/options") for _method, path, _payload in settings.calls)
    asyncio.run(check())


@pytest.mark.parametrize("csrf", ["", "x" * 64, "b" * 64, "ä" * 64])
def test_csrf_token_is_required(settings, csrf):
    async def check():
        headers, payload = await prepare(settings)
        headers["X-Amtron-CSRF"] = csrf
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 403
        assert not any(call[0] == "POST" for call in settings.calls)
    asyncio.run(check())


def test_csrf_is_bound_to_user_and_process(settings):
    async def check():
        headers, payload = await prepare(settings)
        headers["X-Remote-User-Id"] = "b" * 32
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 403
        headers["X-Remote-User-Id"] = USER
        assert (await invoke(FixtureSettings(), request("POST", headers=headers, payload=payload))).status == 403
    asyncio.run(check())


@pytest.mark.parametrize("key", ["token", "read_only", "current_cap", "listen", "port", "host", "supervisor_url"])
def test_usb_save_rejects_any_unrelated_or_unknown_field(settings, key):
    async def check():
        headers, payload = await prepare(settings, "serial")
        payload[key] = "injected"
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 400
        assert body(response)["error"] == "invalid_request"
    asyncio.run(check())


@pytest.mark.parametrize(("key", "value"), [
    ("host", "http://example.test"), ("host", ""), ("host", None), ("host", []),
    ("tcp_port", True), ("tcp_port", 0), ("tcp_port", 1.5),
    ("device_id", True), ("device_id", 51), ("connection_type", []), ("connection_type", "bogus"),
])
def test_malformed_connection_cannot_be_saved(settings, key, value):
    async def check():
        headers, payload = await prepare(settings)
        payload[key] = value
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 400
        assert not any(call[0] == "POST" for call in settings.calls)
    asyncio.run(check())


@pytest.mark.parametrize("serial_port", ["/dev/ttyUSB1", "/dev/null", None, []])
def test_usb_requires_freshly_enumerated_selection(settings, serial_port):
    async def check():
        headers, payload = await prepare(settings, "serial")
        payload["serial_port"] = serial_port
        response = await invoke(settings, request("POST", headers=headers, payload=payload))
        assert response.status == 400
        assert body(response)["error"] == "adapter_missing"
    asyncio.run(check())


def test_changed_options_and_concurrent_saves_do_not_overwrite_each_other(settings):
    async def check():
        headers, payload = await prepare(settings)
        settings.options["current_cap"] = 12
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 409
        headers, payload = await prepare(settings)
        results = await asyncio.gather(*(invoke(settings, request("POST", headers=headers, payload=payload)) for _ in range(2)))
        assert sorted(result.status for result in results) == [200, 409]
        assert settings.options["current_cap"] == 12
    asyncio.run(check())


@pytest.mark.parametrize("revision", [None, [], "bad", "b" * 64])
def test_revision_is_required(settings, revision):
    async def check():
        headers, payload = await prepare(settings)
        payload["revision"] = revision
        assert (await invoke(settings, request("POST", headers=headers, payload=payload))).status == 409
    asyncio.run(check())


def test_invalid_json_content_type_and_shape(settings):
    async def check():
        headers, payload = await prepare(settings)
        response = await invoke(settings, request("POST", headers={**headers, "Content-Type": "text/plain"}, payload=payload))
        assert response.status == 415
        for raw in (b"{", b"[]", b"null", b"[" * 1100):
            req = request("POST", headers=headers, payload={})
            req._read_bytes = raw
            response = await invoke(settings, req)
            assert response.status == 400
    asyncio.run(check())


def test_guard_limits_requests_and_sanitizes_failures(settings, monkeypatch):
    async def check():
        async def fail(_request):
            raise OSError("SECRET PATH TOKEN")
        result = await settings.guard(request(), fail)
        assert body(result) == {"error": "supervisor_unavailable"}
        await settings._slots.acquire()
        await settings._slots.acquire()
        try:
            result = await settings.guard(request(), settings.read)
            assert result.status == 503
            assert body(result) == {"error": "busy"}
        finally:
            settings._slots.release()
            settings._slots.release()
    asyncio.run(check())


@pytest.mark.parametrize("transport", ["serial", "rtu_over_tcp", "modbus_tcp"])
def test_setup_mode_only_for_missing_active_connection(transport):
    result = ui.app_configuration({"connection_type": transport, "token": TOKEN})
    assert isinstance(result, ui.SetupConfig)
    assert TOKEN not in repr(result)
    result = ui.app_configuration({"connection_type": "rtu_over_tcp", "host": "192.0.2.1", "serial_port": "/old/removed/path", "token": TOKEN})
    assert isinstance(result, ServerConfig)


@pytest.mark.parametrize("bad", [
    {"token": "short"}, {"read_only": "true"}, {"current_cap": 99}, {"device_id": True},
    {"connection_type": "serial", "baudrate": 123}, {"frame": "8n1"},
    {"listen": None}, {"port": 0}, {"unknown": True},
    {"connection_type": "rtu_over_tcp", "tcp_port": 0},
    {"state_change_interval_seconds": -1}, {"phase_switch_grace_seconds": 31},
])
def test_setup_does_not_bypass_security_and_control_validation(bad):
    with pytest.raises(ValueError, match=r"token|read_only|current_cap|device_id|baudrate|frame|listen|port|unknown|seconds"):
        ui.app_configuration({"token": TOKEN, **bad})


@pytest.mark.parametrize("path", ["/dev/null", "/etc/passwd", "socket://host:23", "/dev/ttyUSB0/../null", "/dev/serial/by-id/a/b", "", None, [], "/dev/ttyUSB" + "0" * 1024])
def test_native_usb_path_rejects_arbitrary_files_and_urls(path):
    with pytest.raises(ValueError, match="serial_port"):
        ui._serial_path(path)


def test_serial_paths_must_resolve_to_serial_character_devices(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, **_kw: Path("/dev/null"))
    with pytest.raises(ValueError, match="serial character"):
        ui._serial_path("/dev/serial/by-id/wrong")
    monkeypatch.setattr(Path, "resolve", lambda self, **_kw: Path("/dev/ttyUSB0"))
    monkeypatch.setattr(Path, "is_char_device", lambda self: False)
    with pytest.raises(ValueError, match="serial character"):
        ui._serial_path("/dev/ttyUSB0")
    monkeypatch.setattr(Path, "is_char_device", lambda self: True)
    assert ui._serial_path("/dev/ttyUSB0") == "/dev/ttyUSB0"
    def denied(_self, **_kwargs):
        raise PermissionError("hidden path")
    monkeypatch.setattr(Path, "resolve", denied)
    with pytest.raises(ValueError, match="could not be checked"):
        ui._serial_path("/dev/ttyUSB0")


def test_enumeration_prefers_by_id_and_deduplicates_without_sysfs(monkeypatch):
    by_id, by_path = "/dev/serial/by-id/stable", "/dev/serial/by-path/position"
    entries = {"/dev": ["ttyUSB0", "ttyACM1", "null", "ttyUSB2"], "/dev/serial/by-id": ["stable", "broken"], "/dev/serial/by-path": ["position"]}
    monkeypatch.setattr(Path, "is_dir", lambda self: str(self) in entries)
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(self / item for item in entries[str(self)]))
    def resolve(path, **_kwargs):
        if path.name == "broken":
            raise FileNotFoundError
        return Path("/dev/ttyUSB0") if str(path) in (by_id, by_path) else path
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "is_char_device", lambda self: self.name not in ("ttyUSB2", "broken"))
    assert ui.serial_adapters() == [ui.SerialAdapter(by_id, "stable (/dev/ttyUSB0)", "/dev/ttyUSB0"), ui.SerialAdapter("/dev/ttyACM1", "/dev/ttyACM1", "/dev/ttyACM1")]


def test_setup_api_is_live_but_never_ready_or_unauthenticated():
    async def check():
        async with TestClient(TestServer(ui.create_setup_app(ui.SetupConfig("127.0.0.1", 8765, TOKEN)))) as client:
            response = await client.get("/health")
            assert response.status == 200
            assert await response.json() == {"status": "running", "configured": False}
            for path in ("/v2/state", "/v2/command"):
                assert (await client.post(path)).status == 401
                response = await client.post(path, headers={"Authorization": f"Bearer {TOKEN}"})
                assert response.status == 503
                assert await response.json() == {"error": "service_not_configured"}
    asyncio.run(check())


def test_actual_socket_cannot_spoof_ingress_and_assets_use_selected_language(monkeypatch):
    async def check():
        settings = ui.AppSettings("isolated-token", TOKEN)
        async with TestClient(TestServer(ui.create_settings_app(settings))) as client:
            assert settings._session is not None
            response = await client.get("/api/config", headers={**HEADERS, "X-Forwarded-For": ui.SUPERVISOR_PEER})
            assert response.status == 403
            monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
            response = await client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
            assert response.status == 200
            assert 'lang="de"' in await response.text()
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            for path in ("/app_settings.js", "/app_settings.css"):
                assert (await client.get(path)).status == 200
            assert (await client.get("/server.py")).status == 404
            assert (await client.get("/app_settings.js", headers={"Sec-Fetch-Site": "cross-site"})).status == 403
        assert settings._session is None
    asyncio.run(check())


@pytest.mark.parametrize(("header", "expected"), [("", "en"), ("xx,fr-BE;q=0.8", "fr"), ("it", "it"), ("nl-NL", "nl"), ("<script>de</script>", "en")])
def test_language_header_is_normalized(header, expected):
    assert ui._language(header) == expected


@pytest.mark.parametrize(("status", "raw"), [(301, b""), (401, b""), (200, b"{"), (200, b"[]"), (200, b'{"result":"error","data":{}}'), (200, b'{"result":"ok","data":[]}'), (200, b"x" * 65537), (200, b"[" * 1100)])
def test_supervisor_bad_responses_fail_closed(monkeypatch, status, raw):
    async def check():
        async def response(_request):
            return web.Response(body=raw, status=status)
        app = web.Application()
        app.router.add_get("/info", response)
        async with TestServer(app) as server:
            monkeypatch.setattr(ui, "SUPERVISOR_URL", str(server.make_url("")).rstrip("/"))
            settings = ui.AppSettings("isolated-token", TOKEN)
            async for _ in settings.lifecycle(app):
                with pytest.raises(ui.SettingsError, match="supervisor_unavailable"):
                    await settings._request("GET", "/info")
    asyncio.run(check())


def test_supervisor_success_empty_data_missing_options_and_no_session(monkeypatch):
    async def check():
        app = web.Application()
        async def response(req):
            assert req.headers["Authorization"] == "Bearer isolated-token"
            assert await req.json() == {"options": BASE}
            return web.json_response({"result": "ok", "data": {}})
        app.router.add_post("/addons/self/options", response)
        async with TestServer(app) as server:
            monkeypatch.setattr(ui, "SUPERVISOR_URL", str(server.make_url("")).rstrip("/"))
            settings = ui.AppSettings("isolated-token", TOKEN)
            with pytest.raises(ui.SettingsError):
                await settings._request("GET", "/info")
            async for _ in settings.lifecycle(app):
                assert await settings._request("POST", "/addons/self/options", {"options": BASE}) == {}
        async def missing(*_args, **_kwargs):
            return {}
        monkeypatch.setattr(settings, "_request", missing)
        with pytest.raises(ui.SettingsError):
            await settings._options()
        async def failed(*_args, **_kwargs):
            raise ClientConnectionError("private error")
        monkeypatch.setattr(settings, "_request", failed)
        result = await invoke(settings, request())
        assert body(result) == {"error": "supervisor_unavailable"}
    asyncio.run(check())


def test_setup_server_runs_both_listeners_without_modbus_and_stops_cleanly(tmp_path, monkeypatch, caplog):
    import logging
    import signal

    from aiohttp import ClientSession

    from custom_components.mennekes_amtron_rtu.device import server

    async def check():
        sites = []
        ready = asyncio.Event()
        signals = {}
        original_start = web.TCPSite.start

        async def start(site):
            await original_start(site)
            sites.append(site)
            if len(sites) == 2:
                ready.set()

        monkeypatch.setattr(web.TCPSite, "start", start)
        monkeypatch.setattr(server, "INGRESS_PORT", 0)
        monkeypatch.setattr(server, "ModbusService", lambda *_args: pytest.fail("setup opened Modbus"))
        monkeypatch.setattr(server, "publish_discovery", lambda *_args: pytest.fail("setup published discovery"))
        monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda sig, callback, *args: signals.update({sig: (callback, args)}))
        task = asyncio.create_task(server.run(ui.SetupConfig("127.0.0.1", 0, TOKEN), tmp_path / "intent.json", "isolated-supervisor-token"))
        try:
            await asyncio.wait_for(ready.wait(), 2)
            api_port = sites[0]._server.sockets[0].getsockname()[1]
            ingress_port = sites[1]._server.sockets[0].getsockname()[1]
            async with ClientSession() as session:
                response = await session.get(f"http://127.0.0.1:{api_port}/health")
                assert await response.json() == {"status": "running", "configured": False}
                response = await session.get(f"http://127.0.0.1:{api_port}/v2/state", headers={"Authorization": f"Bearer {TOKEN}"})
                assert response.status == 503
                response = await session.get(f"http://127.0.0.1:{ingress_port}/", headers=HEADERS)
                assert response.status == 403
            callback, args = signals[signal.SIGTERM]
            callback(*args)
            await asyncio.wait_for(task, 2)
            assert all(not site._server.is_serving() for site in sites)
            assert not (tmp_path / "intent.json").exists()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    caplog.set_level(logging.INFO)
    asyncio.run(check())
    assert "Connection setup required" in caplog.text
    assert "SIGTERM received" in caplog.text
    assert "Modbus service shutdown complete" in caplog.text
    assert TOKEN not in caplog.text


def test_app_main_uses_setup_mode_for_missing_connection(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device import server

    path = tmp_path / "options.json"
    path.write_text(json.dumps({"connection_type": "LAN: Modbus RTU (TCP)", "host": ""}))
    monkeypatch.setattr("sys.argv", ["amtron-modbus-service", "--config", str(path), "--state", str(tmp_path / "intent.json")])
    monkeypatch.setenv("SUPERVISOR_TOKEN", "isolated-supervisor-token")
    calls = []

    async def run(config, _state, _supervisor):
        calls.append(config)

    monkeypatch.setattr(server, "run", run)
    server.main()
    assert len(calls) == 1
    assert isinstance(calls[0], ui.SetupConfig)
    assert calls[0].token == (tmp_path / "service-token").read_text()


def test_oversized_post_is_rejected_by_real_http_boundary(settings, monkeypatch):
    async def check():
        monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
        async with TestClient(TestServer(ui.create_settings_app(settings))) as client:
            response = await client.get("/api/config", headers=HEADERS)
            config = await response.json()
            response = await client.post("/api/config", headers={**HEADERS, "X-Amtron-CSRF": config["csrf"]}, json={"large": "x" * 5000})
            assert response.status == 413
            assert not any(method == "POST" for method, _path, _payload in settings.calls)
    asyncio.run(check())


def test_native_app_schema_accepts_removed_usb_path_without_extra_privileges():
    import yaml

    metadata = yaml.safe_load(Path("modbus_service/config.yaml").read_text())
    assert metadata["schema"]["serial_port"] == "str?"
    assert metadata["ingress"] is True
    assert metadata["ingress_port"] == ui.INGRESS_PORT
    assert metadata["panel_admin"] is True
    assert not metadata.get("hassio_api")
    assert not metadata.get("host_network")
    assert not metadata.get("full_access")
    assert "hassio_role" not in metadata
    assert "8099/tcp" not in metadata["ports"]
