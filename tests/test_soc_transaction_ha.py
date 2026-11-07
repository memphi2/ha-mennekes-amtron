"""HA retains vehicle stop evidence across a lost real service response."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.vehicle_soc import VehicleSocObserver
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_vehicle_soc import Coordinator


@pytest.mark.parametrize("superseded", ["no", "before_resolve", "before_observe"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_lost_target_reply_is_fenced_before_transient_soc_is_forwarded(tmp_path, superseded, cancelled):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        hass = HomeAssistant(str(tmp_path / "ha"))
        started, release = asyncio.Event(), asyncio.Event()
        original_set = service.control.async_set_soc_goal
        resolved = False
        replaced = False

        async def delayed_set(*args, **kwargs):
            started.set()
            await release.wait()
            await original_set(*args, **kwargs)

        service.control.async_set_soc_goal = delayed_set

        @web.middleware
        async def lose_commit_reply(request, handler):
            nonlocal resolved, replaced
            command = await request.json() if request.path == "/v2/command" else {}
            if not replaced and (
                (request.path == "/v2/soc-goal/resolve" and superseded == "before_resolve")
                or (resolved and superseded == "before_observe" and command.get("command") == "soc_observation")
            ):
                # An independent client explicitly replaces the target after
                # the old command committed, before HA forwards its evidence.
                await service.command({
                    "command": "soc_goal", "expected_serial_number": service.identity.serial_number,
                    "source": "sensor.car|", "value": 90, "soc": 50, "maximum": None,
                })
                replaced = True
            response = await handler(request)
            if request.path == "/v2/soc-goal/resolve":
                resolved = True
            if request.path == "/v2/soc-goal/commit" and not cancelled:
                request.transport.close()
            return response

        app = create_app(service)
        app.middlewares.append(lose_commit_reply)
        observer = None
        try:
            async with TestServer(app) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                hass.states.async_set("sensor.car", "50")
                coordinator = Coordinator()
                observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
                await observer.async_start()
                setting = asyncio.create_task(observer.async_set_target(80))
                await asyncio.wait_for(started.wait(), 3)
                hass.states.async_set("sensor.car", "85")
                hass.states.async_set("sensor.car", "50")
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                if cancelled:
                    setting.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await setting
                    release.set()
                else:
                    release.set()
                    with pytest.raises(HomeAssistantError):
                        await setting
                await hass.async_block_till_done()
                assert not control.soc_goal_resolution_pending
                assert service.control.soc_goal.target_percent == (80 if superseded == "no" else 90)
                assert service.control.soc_goal.status == ("reached" if superseded == "no" else "active")
                if superseded == "before_observe":
                    await remote.update()
                    coordinator.update()
                    await hass.async_block_till_done()
                    assert observer._pending is None
                    assert service.control.soc_goal.status == "active"
        finally:
            release.set()
            if observer is not None:
                await observer.async_stop()
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 20))


def test_older_service_requests_app_update_only_for_percentage_target(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                await control.async_configure_soc("sensor.car|")
                old = deepcopy(remote.snapshot)
                old["control"].pop("soc_transactions_supported")
                old["control"].pop("soc_goal_revision")
                remote.apply_snapshot(old)
                with pytest.raises(ServiceValidationError) as error:
                    await control.async_set_soc_goal(80, source="sensor.car|", soc=50)
                assert error.value.translation_key == "soc_goal_app_update_required"
                # The legacy service can still accept ordinary vehicle metadata.
                await control.async_observe_soc(50, source="sensor.car|")
        finally:
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 20))


def test_explicit_clear_supersedes_a_fenced_unknown_result(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        hass = HomeAssistant(str(tmp_path / "ha"))
        lose_reply = True

        @web.middleware
        async def uncertain_first_commit(request, handler):
            nonlocal lose_reply
            response = await handler(request)
            if request.path == "/v2/soc-goal/commit" and lose_reply:
                lose_reply = False
                request.transport.close()
            if request.path == "/v2/soc-goal/resolve":
                result = await service.soc_transactions.resolve((await request.json())["token"])
                result.update(outcome="unknown", revision=None)
                return web.json_response(result)
            return response

        app = create_app(service)
        app.middlewares.append(uncertain_first_commit)
        observer = None
        try:
            async with TestServer(app) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                hass.states.async_set("sensor.car", "50")
                observer = VehicleSocObserver(hass, control, Coordinator(), "sensor.car")
                await observer.async_start()
                with pytest.raises(HomeAssistantError):
                    await observer.async_set_target(80)
                await hass.async_block_till_done()
                assert control.soc_goal_resolution_fenced
                await observer.async_set_target(0)
                await hass.async_block_till_done()
                assert not control.soc_goal_resolution_pending
                assert service.control.soc_goal.status == "disabled"
        finally:
            if observer is not None:
                await observer.async_stop()
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 20))


def test_outage_during_resolution_preserves_evidence_and_blocks_replacement(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        hass = HomeAssistant(str(tmp_path / "ha"))
        resolve_offline = True
        observations = []

        @web.middleware
        async def drop_then_disconnect(request, handler):
            if request.path == "/v2/soc-goal/resolve" and resolve_offline:
                return web.json_response({"error": "offline"}, status=503)
            if request.path == "/v2/command":
                data = await request.json()
                if data.get("command") == "soc_observation":
                    observations.append(data)
            response = await handler(request)
            if request.path == "/v2/soc-goal/commit":
                request.transport.close()
            return response

        app = create_app(service)
        app.middlewares.append(drop_then_disconnect)
        observer = None
        try:
            async with TestServer(app) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                hass.states.async_set("sensor.car", "50")
                coordinator = Coordinator()
                observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
                await observer.async_start()
                observations.clear()
                with pytest.raises(HomeAssistantError):
                    await observer.async_set_target(80)
                await hass.async_block_till_done()
                assert control.soc_goal_resolution_pending
                assert not control.soc_goal_resolution_fenced
                hass.states.async_set("sensor.car", "85")
                hass.states.async_set("sensor.car", "50")
                await hass.async_block_till_done()
                with pytest.raises(ServiceValidationError) as error:
                    await observer.async_set_target(90)
                assert error.value.translation_key == "soc_goal_unavailable"
                assert not observations
                assert service.control.soc_goal.target_percent == 80
                assert service.control.soc_goal.status == "active"

                resolve_offline = False
                coordinator.update(False)
                coordinator.update(True)
                await hass.async_block_till_done()
                assert not control.soc_goal_resolution_pending
                assert service.control.soc_goal.status == "reached"
                assert any(item["soc"] == 85 for item in observations)
        finally:
            if observer is not None:
                await observer.async_stop()
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 20))


@pytest.mark.parametrize("corrupt", ["capability", "missing_revision", "bad_revision"])
def test_invalid_transaction_capability_never_trusts_snapshot(tmp_path, corrupt):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                snapshot = deepcopy(remote.snapshot)
                if corrupt == "capability":
                    snapshot["control"]["soc_transactions_supported"] = "true"
                elif corrupt == "missing_revision":
                    snapshot["control"].pop("soc_goal_revision")
                else:
                    snapshot["control"]["soc_goal_revision"] = "untrusted"
                with pytest.raises(ServiceConnectionError):
                    remote.apply_snapshot(snapshot)
                assert not remote.connected
                assert service.control.soc_goal.target_percent == 0
        finally:
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 20))


@pytest.mark.parametrize("corrupt", ["reservation", "commit"])
def test_malformed_transaction_response_cannot_silently_apply_target(tmp_path, corrupt):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        hass = HomeAssistant(str(tmp_path / "ha"))

        @web.middleware
        async def invalid_response(request, handler):
            response = await handler(request)
            if corrupt == "reservation" and request.path == "/v2/soc-goal/reserve":
                return web.json_response({"token": "invalid"})
            if corrupt == "commit" and request.path == "/v2/soc-goal/commit":
                return web.json_response({"outcome": "invalid"})
            return response

        app = create_app(service)
        app.middlewares.append(invalid_response)
        observer = None
        try:
            async with TestServer(app) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                hass.states.async_set("sensor.car", "50")
                observer = VehicleSocObserver(hass, control, Coordinator(), "sensor.car")
                await observer.async_start()
                with pytest.raises(HomeAssistantError):
                    await observer.async_set_target(80)
                await hass.async_block_till_done()
                assert not control.soc_goal_resolution_pending
                assert service.control.soc_goal.target_percent == (0 if corrupt == "reservation" else 80)
        finally:
            if observer is not None:
                await observer.async_stop()
            await hass.async_stop()
            await service.stop()

    asyncio.run(asyncio.wait_for(run(), 20))
