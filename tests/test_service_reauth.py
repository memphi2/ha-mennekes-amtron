"""Expired service credentials recover in place without changing the wallbox."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_service_discovery import URL, discovery_info

NEW_TOKEN = "replacement-test-token-32-characters"  # noqa: S105 - test fixture


async def add_entry(hass, url, *, expected_serial="ABC123456789"):
    assert await async_setup_component(hass, "network", {})
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "server"}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
    )
    assert result["type"] == "create_entry", result
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == (expected_serial or f"{DOMAIN}_{url.rstrip('/')}")
    hass.config_entries.async_update_entry(
        entry, options={"current_limit": 10, "scan_interval_seconds": 300}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def entity_ids(hass, entry):
    return {
        entity.unique_id: (entity.entity_id, entity.device_id)
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


async def reauth(hass, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=dict(entry.data),
    )


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("trigger", ["setup", "first_refresh", "poll", "command", "diagnostics"])
def test_auth_failure_starts_real_ha_reauth_and_preserves_entities(
    tmp_path, monkeypatch, caplog, status, trigger
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        allow_stale_once = False

        @web.middleware
        async def reject_expired(request, handler):
            nonlocal allow_stale_once
            if request.headers.get("Authorization") != f"Bearer {service.config.token}":
                if allow_stale_once:
                    allow_stale_once = False
                    return web.json_response(service.snapshot())
                return web.Response(status=status)
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, reject_expired)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(
                    hass, str(server.make_url("/")), expected_serial=service.identity.serial_number
                )
                before = entity_ids(hass, entry)
                old_runtime = entry.runtime_data
                service.config = replace(service.config, token=NEW_TOKEN)
                if trigger in ("setup", "first_refresh"):
                    allow_stale_once = trigger == "first_refresh"
                    assert not await hass.config_entries.async_reload(entry.entry_id)
                    assert entry.state is ConfigEntryState.SETUP_ERROR
                elif trigger == "poll":
                    await old_runtime.coordinator.async_refresh()
                    assert isinstance(
                        old_runtime.coordinator.last_exception, ConfigEntryAuthFailed
                    )
                elif trigger == "command":
                    with pytest.raises(HomeAssistantError) as error:
                        await old_runtime.control.async_pause_charging()
                    assert error.value.translation_key == "service_auth_failed"
                else:
                    with pytest.raises(ServiceConnectionError):
                        await old_runtime.client.request(
                            "GET", "/v2/diagnostics", update_connection=False
                        )
                    assert not old_runtime.client.connected
                await hass.async_block_till_done()
                if trigger in ("command", "diagnostics"):
                    assert not old_runtime.coordinator.last_update_success
                    assert hass.states.get("number.mennekes_amtron_charging_current_limit").state == "unavailable"
                flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                assert len(flows) == 1
                assert flows[0]["step_id"] == "reauth_confirm"
                flow = await hass.config_entries.flow.async_configure(flows[0]["flow_id"])
                assert set(flow["data_schema"].schema) == {"server_token"}
                assert TOKEN not in repr(flow)
                setup = AsyncMock(wraps=integration.async_setup_entry)
                monkeypatch.setattr(integration, "async_setup_entry", setup)
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"server_token": NEW_TOKEN}
                )
                assert result["reason"] == "reauth_successful", result
                await hass.async_block_till_done()
                assert setup.await_count == 1
                assert entry.state is ConfigEntryState.LOADED
                assert entry.data["server_token"] == NEW_TOKEN
                assert entry.options["current_limit"] == 10
                assert entity_ids(hass, entry) == before
                assert len(hass.config_entries.async_entries(DOMAIN)) == 1
                assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                assert not old_runtime.client.connected
                assert old_runtime.client.on_auth_failed is None
                assert not [address for address, _ in transport.writes if address != R.HEARTBEAT.address]
                assert not any(secret in caplog.text for secret in (TOKEN, NEW_TOKEN))
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("update", ["reauth", "entry_data"])
def test_credentials_change_reloads_remote_client_when_costs_are_enabled(tmp_path, monkeypatch, update):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                hass.config_entries.async_update_entry(entry, options={
                    **entry.options, "charging_cost_source": "fixed", "charging_price": 0.3,
                })
                await hass.async_block_till_done()
                old_runtime = entry.runtime_data
                assert old_runtime.charging_cost is not None
                service.config = replace(service.config, token=NEW_TOKEN)
                setup = AsyncMock(wraps=integration.async_setup_entry)
                monkeypatch.setattr(integration, "async_setup_entry", setup)
                if update == "reauth":
                    flow = await reauth(hass, entry)
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_token": NEW_TOKEN},
                    )
                    assert result["reason"] == "reauth_successful"
                else:
                    hass.config_entries.async_update_entry(
                        entry, data={**entry.data, "server_token": NEW_TOKEN},
                    )
                await hass.async_block_till_done()
                setup.assert_awaited_once()
                assert entry.runtime_data is not old_runtime
                assert not old_runtime.client.connected
                assert entry.runtime_data.charging_cost is not None
                await entry.runtime_data.coordinator.async_refresh()
                assert entry.runtime_data.coordinator.last_update_success
                assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                assert not [address for address, _ in transport.writes if address != R.HEARTBEAT.address]
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    "scenario", ["short", "wrong_token", "offline", "not_ready", "invalid", "wrong_device", "missing_serial", "same_token", "legacy"]
)
def test_manual_reauth_validates_credentials_and_identity_without_mutation(
    tmp_path, monkeypatch, scenario
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        if scenario == "legacy":
            service.identity.serial_number = None
        hass = await _boot(tmp_path / "ha")
        fault = None

        @web.middleware
        async def failed_state(request, handler):
            if fault == "offline":
                return web.json_response({}, status=503)
            if fault == "invalid":
                return web.json_response({"api_version": "invalid"})
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, failed_state)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(
                    hass, str(server.make_url("/")), expected_serial=service.identity.serial_number
                )
                before = dict(entry.data)
                original_serial = service.identity.serial_number
                flow = await reauth(hass, entry)
                assert flow["step_id"] == "reauth_confirm"
                token = NEW_TOKEN if scenario != "same_token" else TOKEN
                service.config = replace(service.config, token=token)
                submitted = token
                if scenario == "short":
                    submitted = "short"
                elif scenario == "wrong_token":
                    submitted = TOKEN
                elif scenario == "not_ready":
                    service._success = False
                elif scenario in ("offline", "invalid"):
                    fault = scenario
                elif scenario in ("wrong_device", "missing_serial"):
                    service.identity.serial_number = "other-wallbox" if scenario == "wrong_device" else None
                setup = AsyncMock(wraps=integration.async_setup_entry)
                monkeypatch.setattr(integration, "async_setup_entry", setup)
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"], {"server_token": submitted}
                )
                if scenario in ("same_token", "legacy"):
                    assert result["reason"] == "reauth_successful"
                elif scenario in ("wrong_device", "missing_serial"):
                    assert result["reason"] == "unique_id_mismatch"
                    assert dict(entry.data) == before
                    assert setup.await_count == 0
                else:
                    assert result["errors"]["base"] == {
                        "short": "service_auth_failed", "wrong_token": "service_auth_failed",
                        "offline": "cannot_connect_service", "not_ready": "service_not_ready",
                        "invalid": "service_invalid_response",
                    }[scenario]
                    assert dict(entry.data) == before
                    assert setup.await_count == 0
                    fault = None
                    service._success = True
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_token": token}
                    )
                    assert result["reason"] == "reauth_successful"
                await hass.async_block_till_done()
                if scenario not in ("wrong_device", "missing_serial"):
                    assert setup.await_count == 1
                    assert entry.data["server_token"] == token
                    assert entry.state is ConfigEntryState.LOADED
                    assert entry.data.get("serial_number") == original_serial
                assert transport.writes == []
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    "scenario", ["automatic", "missing", "duplicate", "foreign", "malformed", "stale", "offline", "not_ready", "wrong_device"]
)
def test_app_reauth_uses_only_matching_supervisor_credentials(
    tmp_path, monkeypatch, scenario
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        discoveries = []
        offline = False

        async def discover(_hass):
            return discoveries

        @web.middleware
        async def unavailable(request, handler):
            if offline:
                return web.json_response({}, status=503)
            return await handler(request)

        monkeypatch.setattr(config_flow, "async_app_discoveries", discover)
        app = create_app(service)
        app.middlewares.insert(0, unavailable)
        try:
            async with TestServer(app) as server:
                sessions = []

                class LoopbackRemote(RemoteClient):
                    def __init__(self, session, url, token):
                        assert url == URL
                        sessions.append(session)
                        super().__init__(session, str(server.make_url("/")), token)

                monkeypatch.setattr(config_flow, "RemoteClient", LoopbackRemote)
                monkeypatch.setattr(integration, "RemoteClient", LoopbackRemote)
                entry = await add_entry(hass, URL)
                before = entity_ids(hass, entry)
                service.config = replace(service.config, token=NEW_TOKEN)
                discoveries = [discovery_info(token=NEW_TOKEN)]
                if scenario == "missing":
                    discoveries = []
                elif scenario == "duplicate":
                    discoveries *= 2
                elif scenario == "foreign":
                    discoveries = [discovery_info(token=NEW_TOKEN)]
                    discoveries[0].slug = "12345678_mennekes_amtron_modbus"
                    discoveries[0].config["host"] = discoveries[0].slug.replace("_", "-")
                elif scenario == "malformed":
                    discoveries = [discovery_info(host="foreign-host", token=NEW_TOKEN)]
                elif scenario == "stale":
                    discoveries = [discovery_info()]
                elif scenario == "offline":
                    offline = True
                elif scenario == "not_ready":
                    service._success = False
                elif scenario == "wrong_device":
                    service.identity.serial_number = "other-wallbox"
                setup = AsyncMock(wraps=integration.async_setup_entry)
                monkeypatch.setattr(integration, "async_setup_entry", setup)
                result = await reauth(hass, entry)
                if scenario == "automatic":
                    assert result["reason"] == "reauth_successful"
                elif scenario == "wrong_device":
                    assert result["reason"] == "unique_id_mismatch"
                    assert entry.data["server_token"] == TOKEN
                else:
                    assert result["step_id"] == "reauth_app"
                    assert not result.get("data_schema")
                    assert result["errors"]["base"] == {
                        "stale": "service_auth_failed", "offline": "cannot_connect_service",
                        "not_ready": "service_not_ready",
                    }.get(scenario, "service_discovery_unavailable")
                    assert entry.data["server_token"] == TOKEN
                    assert setup.await_count == 0
                    assert TOKEN not in repr(result)
                    assert NEW_TOKEN not in repr(result)
                    discoveries = [discovery_info(token=NEW_TOKEN)]
                    offline = False
                    service._success = True
                    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
                    assert result["reason"] == "reauth_successful"
                await hass.async_block_till_done()
                if scenario != "wrong_device":
                    assert setup.await_count == 1
                    assert entry.data["server_token"] == NEW_TOKEN
                    assert entry.state is ConfigEntryState.LOADED
                else:
                    assert setup.await_count == 0
                assert entity_ids(hass, entry) == before
                assert all(session is sessions[0] for session in sessions)
                assert not sessions[0].closed
                assert not transport.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_direct_entry_has_no_credential_reauth(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(entry, data={"port": "/dev/fake"})
                result = await reauth(hass, entry)
                assert result["reason"] == "reauth_not_supported"
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
