"""Independently released services must satisfy API and minimum-version contracts."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu import remote_compat
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_version import SERVICE_VERSION
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import TOKEN, current_writes, service_fixture
from tests.test_service_reauth import add_entry


@pytest.mark.parametrize("phase", ["flow", "setup", "poll"])
@pytest.mark.parametrize("version", [1, 3, "missing_route", True, "2"])
def test_api_mismatch_is_distinct_from_auth_failure_and_recovers(
    tmp_path, phase, version
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path, read_only=True)
        hass = await _boot(tmp_path / "ha")
        incompatible = phase == "flow"
        paths = []

        @web.middleware
        async def older_service(request, handler):
            paths.append(request.path)
            if incompatible and request.path == "/v2/state":
                if version == "missing_route":
                    return web.Response(status=404)
                payload = service.snapshot()
                payload["api_version"] = version
                return web.json_response(payload)
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, older_service)
        expected = (
            "service_version_mismatch"
            if type(version) is int or version == "missing_route" else "service_invalid_response"
        )
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(app) as server:
                url = str(server.make_url("/"))
                if phase == "flow":
                    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                    flow = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"next_step_id": "server"}
                    )
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
                    )
                    assert result["errors"] == {"base": expected}
                    assert not hass.config_entries.async_entries(DOMAIN)
                    incompatible = False
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
                    )
                    assert result["type"] == "create_entry"
                    await hass.async_block_till_done()
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                else:
                    entry = await add_entry(hass, url)
                    incompatible = True
                    if phase == "setup":
                        assert not await hass.config_entries.async_reload(entry.entry_id)
                        assert entry.state is ConfigEntryState.SETUP_RETRY
                    else:
                        await entry.runtime_data.coordinator.async_refresh()
                        assert not entry.runtime_data.coordinator.last_update_success
                        if expected == "service_version_mismatch":
                            assert entry.runtime_data.coordinator.last_exception.translation_key == expected
                    await hass.async_block_till_done()
                    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                    incompatible = False
                    if phase == "setup":
                        assert await hass.config_entries.async_reload(entry.entry_id)
                    else:
                        await entry.runtime_data.coordinator.async_refresh()
                assert entry.unique_id == "ABC123456789"
                assert entry.state is ConfigEntryState.LOADED
                assert entry.runtime_data.coordinator.last_update_success
                assert all(path.startswith("/v2/") for path in paths)
                assert not bus.writes
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(("advertised", "minimum", "error"), [
    ("missing", "0.5.0", "service_invalid_response"),
    ("missing", "0.5.1", "service_invalid_response"),
    ("0.5.0", "0.5.0", None),
    ("0.10.0", "0.9.0", None),
    ("0.9.0", "0.10.0", "service_version_mismatch"),
    ("0.5.0b1", "0.5.0", "service_version_mismatch"),
    ("0.5.0-beta.1", "0.5.0", "service_version_mismatch"),
    ("0.5.0-rc.1", "0.5.0", "service_version_mismatch"),
    ("0.5.0.dev1", "0.5.0", "service_version_mismatch"),
    ("0.6.0-beta.1", "0.5.0", None),
    ("0.6.0-beta.10", "0.6.0b2", None),
    ("0.6.0b2", "0.6.0-beta.10", "service_version_mismatch"),
    ("0.5.0.post1", "0.5.0", None),
    ("0.5.0+build.1", "0.5.0", None),
    ("", "0.5.0", "service_invalid_response"),
    ("latest", "0.5.0", "service_invalid_response"),
    (None, "0.5.0", "service_invalid_response"),
    (True, "0.5.0", "service_invalid_response"),
    (5, "0.5.0", "service_invalid_response"),
    ([], "0.5.0", "service_invalid_response"),
    ({}, "0.5.0", "service_invalid_response"),
    ("1" * 129, "0.5.0", "service_invalid_response"),
])
def test_service_versions_use_numeric_and_prerelease_ordering(
    tmp_path, monkeypatch, advertised, minimum, error
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path, read_only=True)
        monkeypatch.setattr(remote_compat, "MIN_SERVICE_VERSION", minimum)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                # Seed a known compatible snapshot before changing its version.
                payload["service_version"] = minimum
                remote.apply_snapshot(payload)
                accepted = deepcopy(remote.snapshot)
                incoming = deepcopy(payload)
                if advertised == "missing":
                    incoming.pop("service_version")
                else:
                    incoming["service_version"] = advertised
                updates = []
                reauth = []
                remote.on_update = lambda: updates.append(remote.connected)
                remote.on_auth_failed = lambda: reauth.append(True)
                if error:
                    with pytest.raises(ServiceConnectionError) as failure:
                        remote.apply_snapshot(incoming)
                    assert failure.value.reason == error
                    assert remote.snapshot == accepted
                    assert updates == [False]
                    assert not remote.connected
                else:
                    remote.apply_snapshot(incoming)
                    assert remote.connected
                    assert updates == [True]
                assert not reauth
                assert not bus.writes
                await remote.async_close()
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("advertised", ["0.9.0b4", "0.10.0", "0.9.0", "0.9.0b5"])
def test_supported_services_work_without_a_matching_integration_version(tmp_path, advertised):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        paths = []

        @web.middleware
        async def independent_version(request, handler):
            paths.append(request.path)
            response = await handler(request)
            if request.path in ("/v2/state", "/v2/command") and response.status == 200:
                import json

                payload = json.loads(response.body)
                assert payload["service_version"] == SERVICE_VERSION
                if advertised == "missing":
                    payload.pop("service_version")
                else:
                    payload["service_version"] = advertised
                return web.json_response(payload)
            return response

        app = create_app(service)
        app.middlewares.append(independent_version)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                assert entry.state is ConfigEntryState.LOADED
                assert not current_writes(bus)
                await entry.runtime_data.control.async_set_charging_current(10)
                assert current_writes(bus) == [10]
                assert paths.count("/v2/command") == 1
                assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize("phase", ["flow", "setup", "poll", "command"])
@pytest.mark.parametrize(("advertised", "minimum", "expected"), [
    ("0.4.9", "0.5.0", "service_version_mismatch"),
    ("missing", "0.5.1", "service_invalid_response"),
    (None, "0.5.0", "service_invalid_response"),
])
def test_incompatible_service_version_blocks_and_recovers_without_reauth_or_replay(
    tmp_path, monkeypatch, phase, advertised, minimum, expected
):
    async def run():
        service, bus, _clock = await service_fixture(tmp_path)
        monkeypatch.setattr(remote_compat, "MIN_SERVICE_VERSION", minimum)
        hass = await _boot(tmp_path / "ha")
        incompatible = phase == "flow"
        requests = []

        @web.middleware
        async def version_response(request, handler):
            requests.append((request.method, request.path))
            if request.path == "/v2/state" or (incompatible and request.path == "/v2/command"):
                # For a command response an uncertain write must not be retried.
                payload = service.snapshot()
                payload["service_version"] = minimum
                if incompatible:
                    if advertised == "missing":
                        payload.pop("service_version")
                    else:
                        payload["service_version"] = advertised
                return web.json_response(payload)
            return await handler(request)

        app = create_app(service)
        app.middlewares.append(version_response)
        try:
            assert await async_setup_component(hass, "network", {})
            async with TestServer(app) as server:
                url = str(server.make_url("/"))
                if phase == "flow":
                    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                    flow = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"next_step_id": "server"}
                    )
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
                    )
                    assert result["errors"] == {"base": expected}
                    assert not hass.config_entries.async_entries(DOMAIN)
                    incompatible = False
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"server_url": url, "server_token": TOKEN}
                    )
                    assert result["type"] == "create_entry"
                    await hass.async_block_till_done()
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                else:
                    entry = await add_entry(hass, url)
                    incompatible = True
                    requests.clear()
                    if phase == "setup":
                        assert not await hass.config_entries.async_reload(entry.entry_id)
                        assert entry.state is ConfigEntryState.SETUP_RETRY
                    else:
                        runtime = entry.runtime_data
                        if phase == "command":
                            with pytest.raises(HomeAssistantError) as error:
                                await runtime.control.async_set_charging_current(10)
                            assert error.value.translation_key == (
                                expected if expected == "service_version_mismatch" else "service_unavailable"
                            )
                            assert requests == [("POST", "/v2/command")]
                        else:
                            await runtime.coordinator.async_refresh()
                        await hass.async_block_till_done()
                        assert not runtime.client.connected
                        assert not runtime.coordinator.last_update_success
                        assert hass.states.get("number.mennekes_amtron_charging_current_limit").state == "unavailable"
                        accepted = len(requests)
                        with pytest.raises(HomeAssistantError):
                            await runtime.control.async_set_charging_current(12)
                        assert len(requests) == accepted
                    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                    incompatible = False
                    if phase == "setup":
                        assert await hass.config_entries.async_reload(entry.entry_id)
                    else:
                        await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert entry.unique_id == "ABC123456789"
                assert entry.state is ConfigEntryState.LOADED
                assert entry.runtime_data.coordinator.last_update_success
                assert hass.states.get("number.mennekes_amtron_charging_current_limit").state != "unavailable"
                assert not current_writes(bus)
                assert requests.count(("POST", "/v2/command")) == (1 if phase == "command" else 0)
                assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))
