"""Retry optional setup failures without losing authentication failures."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import current_writes, service_fixture
from tests.test_service_reauth import add_entry
from tests.test_soc_goal_ha_lifecycle import _entry

SOC = "sensor.optional_setup_car_soc"


@pytest.mark.parametrize("stage", ["vehicle", "departure", "disabled_departure"])
@pytest.mark.parametrize("failure", ["unavailable", "auth"])
def test_optional_remote_setup_retries_or_requests_reauthentication(
    tmp_path, stage, failure,
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        target = "soc_source" if stage == "vehicle" else "cancel_timed_current"
        failing = False
        attempts = []

        @web.middleware
        async def fail_once(request, handler):
            nonlocal failing
            if request.path == "/v2/command":
                command = (await request.json()).get("command")
                attempts.append(command)
                if failing and command == target:
                    failing = False
                    return web.json_response({}, status=401 if failure == "auth" else 503)
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, fail_once)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                assert await hass.config_entries.async_unload(entry.entry_id)
                extra = (
                    {"timed_goal_enabled": False, "timed_goal_reset_pending": True}
                    if stage == "disabled_departure" else
                    {"vehicle_soc_entity": SOC, "timed_goal_enabled": True}
                    if stage == "departure" else {"vehicle_soc_entity": SOC}
                )
                hass.config_entries.async_update_entry(entry, options={**entry.options, **extra})
                writes_before = current_writes(transport)
                failing = True
                assert not await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()

                assert not failing
                failed_runtime = entry.runtime_data
                assert not failed_runtime.client.connected
                assert failed_runtime.client.on_auth_failed is None
                if failed_runtime.heartbeat is not None:
                    assert not failed_runtime.heartbeat.running
                if failed_runtime.vehicle_soc is not None:
                    assert failed_runtime.vehicle_soc._stopping
                assert current_writes(transport) == writes_before

                flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
                if failure == "auth":
                    assert entry.state is ConfigEntryState.SETUP_ERROR
                    assert entry._async_cancel_retry_setup is None
                    assert len(flows) == 1
                    assert flows[0]["step_id"] == "reauth_confirm"
                else:
                    assert entry.state is ConfigEntryState.SETUP_RETRY
                    assert entry._async_cancel_retry_setup is not None
                    assert flows == []
                    # Execute the same locked setup HA's scheduled retry uses.
                    entry.async_cancel_retry_setup()
                    await entry.async_setup_locked(hass)
                    await hass.async_block_till_done()
                    assert entry.state is ConfigEntryState.LOADED
                    assert current_writes(transport) == writes_before
                    assert attempts.count(target) >= 2
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_lost_source_reply_recovers_from_authoritative_snapshot(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        lose_reply = False
        source_requests = 0

        @web.middleware
        async def lose_one_reply(request, handler):
            nonlocal lose_reply, source_requests
            if request.path == "/v2/command" and (await request.json()).get("command") == "soc_source":
                source_requests += 1
                if lose_reply:
                    lose_reply = False
                    response = await handler(request)
                    assert response.status == 200
                    return web.json_response({}, status=503)
            return await handler(request)

        app = create_app(service)
        app.middlewares.insert(0, lose_one_reply)
        try:
            async with TestServer(app) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, "vehicle_soc_entity": SOC},
                )
                writes_before = current_writes(transport)
                lose_reply = True
                assert not await hass.config_entries.async_setup(entry.entry_id)
                assert entry.state is ConfigEntryState.SETUP_RETRY
                assert service.control.soc_goal.source == f"{SOC}|"
                assert source_requests == 1
                entry.async_cancel_retry_setup()
                await entry.async_setup_locked(hass)
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert source_requests == 1
                assert entry.runtime_data.control.soc_goal.source == f"{SOC}|"
                assert current_writes(transport) == writes_before
        finally:
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 60))


def test_vehicle_source_storage_failure_defers_direct_setup(tmp_path, monkeypatch):
    async def run():
        transport = FakeModbusClient(device_bank())
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                hass.config_entries.async_update_entry(
                    entry, options={
                        **entry.options, "vehicle_soc_entity": SOC,
                        "control_mode": CONTROL_MODE_MASTER,
                    },
                )
                original_save = integration.SocGoalStore.async_save
                monkeypatch.setattr(
                    integration.SocGoalStore, "async_save", AsyncMock(side_effect=OSError("temporary")),
                )
                assert not await hass.config_entries.async_setup(entry.entry_id)
                assert entry.state is ConfigEntryState.SETUP_RETRY
                assert entry._async_cancel_retry_setup is not None
                assert entry.runtime_data.vehicle_soc._stopping
                assert not entry.runtime_data.client.connected
                assert not entry.runtime_data.heartbeat.running
                monkeypatch.setattr(integration.SocGoalStore, "async_save", original_save)
                entry.async_cancel_retry_setup()
                await entry.async_setup_locked(hass)
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert entry.runtime_data.control.soc_goal.source == f"{SOC}|"
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
