"""Configuration recovers from errors and safely handles concurrent discovery."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.ha_fakes import FakeHass
from tests.test_config_flow import SearchFlow
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_service_discovery import URL, discovery_info
from tests.test_service_reauth import NEW_TOKEN, add_entry, entity_ids


@pytest.mark.parametrize("source", ["user", "hassio"])
@pytest.mark.parametrize("failure", ["authentication", "unavailable", "not_ready"])
def test_failed_service_setup_can_recover_in_the_same_flow(
    tmp_path, monkeypatch, source, failure
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        failing = True

        @web.middleware
        async def temporary_failure(request, handler):
            if failing:
                if failure == "authentication":
                    return web.Response(status=401)
                if failure == "unavailable":
                    return web.json_response({}, status=503)
                snapshot = service.snapshot()
                snapshot["available"] = False
                return web.json_response(snapshot)
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, temporary_failure)
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(app) as server:

                class LoopbackRemote(RemoteClient):
                    def __init__(self, session, _url, token):
                        super().__init__(session, str(server.make_url("/")), token)

                monkeypatch.setattr(config_flow, "RemoteClient", LoopbackRemote)
                monkeypatch.setattr(integration, "RemoteClient", LoopbackRemote)
                if source == "user":
                    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                    flow = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"next_step_id": "server"}
                    )
                    submission = {"server_url": URL, "server_token": TOKEN}
                    result = await hass.config_entries.flow.async_configure(flow["flow_id"], submission)
                else:
                    submission = {}
                    result = await hass.config_entries.flow.async_init(
                        DOMAIN, context={"source": "hassio"}, data=discovery_info()
                    )
                flow_id = result["flow_id"]
                assert result["errors"]["base"] == {
                    "authentication": "service_auth_failed",
                    "unavailable": "cannot_connect_service",
                    "not_ready": "service_not_ready",
                }[failure]
                assert not hass.config_entries.async_entries(DOMAIN)
                failing = False
                result = await hass.config_entries.flow.async_configure(flow_id, submission)
                assert result["type"] == "create_entry"
                await hass.async_block_till_done()
                entries = hass.config_entries.async_entries(DOMAIN)
                assert len(entries) == 1
                assert entries[0].unique_id == "ABC123456789"
                assert entries[0].state is ConfigEntryState.LOADED
                assert not transport.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_multiple_apps_require_explicit_discovery_selection(tmp_path, monkeypatch):
    async def run():
        hass = await _boot(tmp_path / "ha")
        monkeypatch.setattr(
            config_flow, "async_app_discoveries", AsyncMock(return_value=[discovery_info(), discovery_info()])
        )
        monkeypatch.setattr(config_flow, "RemoteClient", lambda *_: pytest.fail("must not guess which app to probe"))
        try:
            flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
            result = await hass.config_entries.flow.async_configure(
                flow["flow_id"], {"next_step_id": "server"}
            )
            assert result["reason"] == "multiple_service_apps"
            assert not hass.config_entries.async_entries(DOMAIN)
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_discovery_preserves_an_unrelated_pending_user_flow(tmp_path, monkeypatch):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(create_app(service)) as server:

                class LoopbackRemote(RemoteClient):
                    def __init__(self, session, _url, token):
                        super().__init__(session, str(server.make_url("/")), token)

                monkeypatch.setattr(config_flow, "RemoteClient", LoopbackRemote)
                monkeypatch.setattr(integration, "RemoteClient", LoopbackRemote)
                user_flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                discovered = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "hassio"}, data=discovery_info()
                )
                assert {flow["flow_id"] for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)} == {
                    user_flow["flow_id"], discovered["flow_id"]
                }
                created = await hass.config_entries.flow.async_configure(discovered["flow_id"], {})
                assert created["type"] == "create_entry"
                await hass.async_block_till_done()
                assert hass.config_entries.async_entries(DOMAIN)[0].unique_id == "ABC123456789"
                assert not transport.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_discovered_token_rotation_reloads_a_loaded_entry_once(tmp_path, monkeypatch):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:

                class LoopbackRemote(RemoteClient):
                    def __init__(self, session, _url, token):
                        super().__init__(session, str(server.make_url("/")), token)

                monkeypatch.setattr(config_flow, "RemoteClient", LoopbackRemote)
                monkeypatch.setattr(integration, "RemoteClient", LoopbackRemote)
                entry = await add_entry(hass, URL)
                before = entity_ids(hass, entry)
                service.config = replace(service.config, token=NEW_TOKEN)
                setup = AsyncMock(wraps=integration.async_setup_entry)
                monkeypatch.setattr(integration, "async_setup_entry", setup)
                assert entry.update_listeners
                result = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "hassio"}, data=discovery_info(token=NEW_TOKEN)
                )
                assert result["reason"] == "service_discovery_updated"
                await hass.async_block_till_done()
                assert setup.await_count == 1
                assert entry.unique_id == "ABC123456789"
                assert entry.data["server_token"] == NEW_TOKEN
                assert entity_ids(hass, entry) == before
                assert not transport.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_discovery_confirmation_without_private_payload_is_rejected(monkeypatch):
    async def run():
        flow = SearchFlow(FakeHass())
        monkeypatch.setattr(config_flow, "RemoteClient", lambda *_: pytest.fail("missing discovery must not connect"))
        result = await flow.async_step_hassio_confirm()
        assert result["reason"] == "invalid_service_discovery"

    asyncio.run(run())


def test_repeated_search_poll_without_a_task_returns_to_the_form():
    async def run():
        flow = SearchFlow(FakeHass())
        for _ in range(2):
            assert (await flow.async_step_search())["next_step_id"] == "search_result"
        result = await flow.async_step_search_result()
        assert result["step_id"] == "serial"
        assert result["errors"] == {"base": "not_found"}

    asyncio.run(run())
