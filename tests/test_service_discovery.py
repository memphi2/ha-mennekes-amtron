"""Supervisor discovery must carry credentials privately and preserve device identity."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.setup import async_setup_component

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device import service_discovery as discovery
from custom_components.mennekes_amtron_rtu.device.client import SerialConfig
from custom_components.mennekes_amtron_rtu.device.const import (
    DOMAIN,
    SUPPORTED_BAUDRATES,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, current_writes, service_fixture

SLUG = "a87b2ffe_mennekes_amtron_modbus"
HOST = SLUG.replace("_", "-")
URL = f"http://{HOST}:8765"


@pytest.mark.parametrize("baudrate", SUPPORTED_BAUDRATES)
def test_supervisor_baudrate_choices_are_accepted_as_text(baudrate):
    config = ServerConfig.from_dict(
        {"serial_port": "/dev/fake", "token": TOKEN, "baudrate": str(baudrate)}
    )
    assert config.serial.baudrate == baudrate


@pytest.mark.parametrize(
    "baudrate", [True, 57600.5, "57600.0", "57600x", " 57600", "115200"]
)
def test_invalid_baudrates_remain_rejected(baudrate):
    with pytest.raises(ValueError, match=r"integer|baudrate"):
        ServerConfig.from_dict(
            {"serial_port": "/dev/fake", "token": TOKEN, "baudrate": baudrate}
        )


def discovery_info(**changes):
    return HassioServiceInfo(
        config={"host": HOST, "port": 8765, "token": TOKEN, **changes},
        name="Mennekes Amtron Modbus Service",
        slug=SLUG,
        uuid="1" * 32,
    )


def test_generated_app_token_is_private_persistent_and_not_rewritten(tmp_path):
    path = tmp_path / "data/service-token"
    token = discovery.app_token(path)
    assert len(token) >= 32
    assert path.stat().st_mode & 0o777 == 0o600
    before = path.stat().st_mtime_ns
    assert discovery.app_token(path) == token
    assert path.stat().st_mtime_ns == before
    assert not list(path.parent.glob(".token-*"))
    path.write_text("broken")
    with pytest.raises(ValueError, match="token must"):
        discovery.app_token(path)


@pytest.mark.parametrize("configured", [False, True])
def test_app_cli_owns_credentials_and_ignores_legacy_manual_tokens(
    tmp_path, monkeypatch, configured
):
    from custom_components.mennekes_amtron_rtu.device import server

    options = tmp_path / "options.json"
    options.write_text(
        json.dumps({"serial_port": "/dev/fake", "token": TOKEN if configured else ""})
    )
    state = tmp_path / "intent.json"
    monkeypatch.setattr(
        "sys.argv",
        ["amtron-modbus-service", "--config", str(options), "--state", str(state)],
    )
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-private")
    calls = []

    async def run(config, actual_state, supervisor_token):
        calls.append((config.token, actual_state, supervisor_token))

    monkeypatch.setattr(server, "run", run)
    server.main()
    server.main()
    assert calls[0] == calls[1]
    assert calls[0][1:] == (state, "supervisor-private")
    assert calls[0][0] == (tmp_path / "service-token").read_text()
    assert calls[0][0] != TOKEN


def test_discovery_rejects_foreign_app_hostname_and_cancels_promptly(monkeypatch):
    async def run():
        queried = asyncio.Event()

        async def request(_session, method, path, **_kwargs):
            assert (method, path) == ("GET", "/addons/self/info")
            queried.set()
            return {"hostname": "foreign-app"}

        monkeypatch.setattr(discovery, "_request", request)
        task = asyncio.create_task(
            discovery.publish_discovery(
                ServerConfig(SerialConfig("/dev/fake"), TOKEN), "supervisor-private"
            )
        )
        await asyncio.wait_for(queried.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)

    asyncio.run(run())


def test_discovery_retries_then_registers_once_without_logging_secrets(
    monkeypatch, caplog
):
    async def run():
        config = ServerConfig(SerialConfig("/dev/fake"), TOKEN)
        attempts = 0
        announcements = []

        async def info(request):
            nonlocal attempts
            assert request.headers["Authorization"] == "Bearer supervisor-private"
            attempts += 1
            if attempts == 1:
                return web.Response(status=503, text=TOKEN)
            return web.json_response({"result": "ok", "data": {"hostname": HOST}})

        async def announce(request):
            announcements.append(await request.json())
            return web.json_response({"result": "ok", "data": {"uuid": "1" * 32}})

        app = web.Application()
        app.router.add_get("/addons/self/info", info)
        app.router.add_post("/discovery", announce)
        async with TestServer(app) as server:
            monkeypatch.setattr(discovery, "SUPERVISOR_URL", str(server.make_url("")))
            monkeypatch.setattr(discovery, "RETRY_SECONDS", 0.001)
            await asyncio.wait_for(
                discovery.publish_discovery(config, "supervisor-private"), 2
            )
        assert attempts == 2
        assert announcements == [
            {"service": DOMAIN, "config": {"host": HOST, "port": 8765, "token": TOKEN}}
        ]
        assert caplog.text.count("Supervisor discovery unavailable") == 1
        assert TOKEN not in caplog.text
        assert "supervisor-private" not in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize(
    "response", ["redirect", "large", "invalid_json", "wrong_result", "wrong_data"]
)
def test_supervisor_responses_are_bounded_and_redirects_are_not_followed(
    monkeypatch, response
):
    async def run():
        async def handler(_request):
            if response == "redirect":
                return web.Response(status=302, headers={"Location": "/secret"})
            if response == "large":
                return web.Response(body=b" " * 65537)
            if response == "invalid_json":
                return web.Response(text="{")
            if response == "wrong_result":
                return web.json_response({"result": "error"})
            return web.json_response({"result": "ok", "data": []})

        app = web.Application()
        app.router.add_get("/test", handler)
        async with TestServer(app) as server, ClientSession() as session:
            monkeypatch.setattr(discovery, "SUPERVISOR_URL", str(server.make_url("")))
            with pytest.raises(ValueError, match=r"Supervisor|JSON|property name"):
                await discovery._request(session, "GET", "/test")

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [{"host": "attacker.example"}, {"port": True}, {"port": 1234}, {"token": "short"}],
)
def test_invalid_discovery_never_sends_credentials(tmp_path, change):
    async def run():
        hass = await _boot(tmp_path / "ha")
        try:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "hassio"}, data=discovery_info(**change)
            )
            assert result["type"] == "abort"
            assert result["reason"] == "invalid_service_discovery"
        finally:
            await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("existing", ["new", "remote", "direct", "disabled"])
def test_real_ha_discovery_connects_without_url_or_token_fields_and_keeps_ids(
    tmp_path, monkeypatch, existing
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        assert await async_setup_component(hass, "network", {})
        try:
            async with TestServer(create_app(service)) as server:

                class LoopbackRemote(RemoteClient):
                    def __init__(self, session, _url, token):
                        super().__init__(session, str(server.make_url("/")), token)

                monkeypatch.setattr(config_flow, "RemoteClient", LoopbackRemote)
                monkeypatch.setattr(integration, "RemoteClient", LoopbackRemote)
                entry = None
                if existing != "new":
                    flow = await hass.config_entries.flow.async_init(
                        DOMAIN, context={"source": "user"}
                    )
                    flow = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"next_step_id": "server"}
                    )
                    await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": URL, "server_token": TOKEN}
                    )
                    await hass.async_block_till_done()
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                    from homeassistant.helpers import entity_registry as er

                    registry = er.async_get(hass)
                    before = {
                        item.unique_id: (item.entity_id, item.device_id)
                        for item in er.async_entries_for_config_entry(
                            registry, entry.entry_id
                        )
                    }
                    assert before
                    if existing in ("direct", "disabled"):
                        await hass.config_entries.async_unload(entry.entry_id)
                        hass.config_entries.async_update_entry(
                            entry,
                            data={
                                "port": "/dev/previous",
                                "serial_number": entry.unique_id,
                            },
                        )
                        if existing == "disabled":
                            from homeassistant.config_entries import ConfigEntryDisabler

                            await hass.config_entries.async_set_disabled_by(
                                entry.entry_id, ConfigEntryDisabler.USER
                            )
                    else:
                        # A changed app token is refreshed automatically, after
                        # checking the same wallbox with the new credentials.
                        hass.config_entries.async_update_entry(
                            entry, data={**entry.data, "server_token": "old" * 16}
                        )
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "hassio"}, data=discovery_info()
                )
                if existing == "remote":
                    assert result["type"] == "abort"
                else:
                    assert result["type"] == "form"
                    assert result["step_id"] == "hassio_confirm"
                    assert not result.get("data_schema")
                    result = await hass.config_entries.flow.async_configure(
                        result["flow_id"], {}
                    )
                    assert result["type"] == (
                        "create_entry" if existing == "new" else "abort"
                    )
                await hass.async_block_till_done()
                entries = hass.config_entries.async_entries(DOMAIN)
                assert len(entries) == 1
                connected = entries[0]
                if existing != "disabled":
                    from homeassistant.config_entries import ConfigEntryState

                    assert connected.state is ConfigEntryState.LOADED
                assert connected.data["server_url"] == URL
                assert connected.data["server_token"] == TOKEN
                assert "port" not in connected.data
                if entry is not None:
                    assert connected.entry_id == entry.entry_id
                    after = {
                        item.unique_id: (item.entity_id, item.device_id)
                        for item in er.async_entries_for_config_entry(
                            registry, entry.entry_id
                        )
                    }
                    assert before.items() <= after.items()
                if existing == "disabled":
                    assert connected.disabled_by is not None
                repeat = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "hassio"}, data=discovery_info()
                )
                assert repeat["reason"] == "already_configured"
                assert not current_writes(transport)
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    "scenario",
    ["unready", "no_serial", "changed_identity", "ignored", "new_credentials"],
)
def test_discovery_handles_incomplete_setup_and_identity_safely(
    tmp_path, monkeypatch, scenario
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        assert await async_setup_component(hass, "network", {})
        try:
            async with TestServer(create_app(service)) as server:

                class LoopbackRemote(RemoteClient):
                    def __init__(self, session, _url, token):
                        super().__init__(session, str(server.make_url("/")), token)

                monkeypatch.setattr(config_flow, "RemoteClient", LoopbackRemote)
                monkeypatch.setattr(integration, "RemoteClient", LoopbackRemote)
                serial = service.identity.serial_number
                if scenario == "ignored":
                    await hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": "ignore"},
                        data={"unique_id": serial, "title": "Ignored wallbox"},
                    )
                if scenario == "no_serial":
                    service.identity.serial_number = None
                if scenario == "unready":
                    service._success = False
                data = (
                    discovery_info(token="old" * 16)
                    if scenario == "new_credentials"
                    else discovery_info()
                )
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "hassio"}, data=data
                )
                if scenario == "ignored":
                    assert result["reason"] == "already_configured"
                    return
                assert result["type"] == "form"
                if scenario in ("unready", "new_credentials"):
                    assert result["errors"] == {"base": "cannot_connect_service"}
                elif scenario == "no_serial":
                    assert result["errors"] == {
                        "base": "service_discovery_requires_serial"
                    }
                if scenario == "changed_identity":
                    service.identity.serial_number = "OTHER-WALLBOX"
                    result = await hass.config_entries.flow.async_configure(
                        result["flow_id"], {}
                    )
                    assert result["reason"] == "service_identity_changed"
                    assert not hass.config_entries.async_entries(DOMAIN)
                    return
                service._success = True
                service.identity.serial_number = serial
                if scenario == "new_credentials":
                    old_flow = result["flow_id"]
                    result = await hass.config_entries.flow.async_init(
                        DOMAIN, context={"source": "hassio"}, data=discovery_info()
                    )
                    assert result["flow_id"] != old_flow
                    assert (
                        len(hass.config_entries.flow.async_progress_by_handler(DOMAIN))
                        == 1
                    )
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {}
                )
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                assert len(hass.config_entries.async_entries(DOMAIN)) == 1
                assert not current_writes(transport)
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(run())
