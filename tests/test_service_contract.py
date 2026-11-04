"""Exercise the authenticated API boundary and every exposed HA control."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronWriteRejectedError,
)
from custom_components.mennekes_amtron_rtu.device.data import (
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron_rtu.device.enums import (
    RequestedPhases,
    SolarChargingMode,
)
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
    DeviceValidationError,
)
from custom_components.mennekes_amtron_rtu.device.register_blocks import REGISTER_BLOCKS
from custom_components.mennekes_amtron_rtu.device.runtime_events import EventType
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


def test_a_delayed_poll_cannot_undo_a_newer_command_snapshot(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        poll_started = asyncio.Event()
        release_poll = asyncio.Event()
        requests = []

        async def request(method, _path, payload=None):
            requests.append(method)
            if method == "GET":
                stale = deepcopy(service.snapshot())
                poll_started.set()
                await release_poll.wait()
                return stale
            return await service.command(payload)

        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://service:8765", TOKEN)
                remote.request = request
                poll = asyncio.create_task(remote.update())
                await asyncio.wait_for(poll_started.wait(), 2)
                command = asyncio.create_task(remote.execute("pause"))
                await asyncio.sleep(0)
                assert requests == ["GET"]
                assert not command.done()
                release_poll.set()
                await asyncio.wait_for(asyncio.gather(poll, command), 2)
                assert remote.write_state.pause_requested is True
                assert remote.write_state.confirmed_current == 1
                assert current_writes(transport) == [1]
        finally:
            release_poll.set()
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_version", 2),
        ("api_version", True),
        ("values", []),
        ("values", {"x": float("nan")}),
        ("control.read_only", None),
        ("control.pause_requested", 1),
        ("control.charging_setpoint", 3),
        ("control.max_charging_current", 33),
        ("control.current_cap", False),
        ("identity.layout_version", 0),
        ("identity.serial_number", 123),
        ("identity.max_evse_current", -1),
        ("identity.phase_options_hw", 65536),
        ("identity.phase_options_hw", True),
        ("heartbeat", {}),
        ("diagnostics", []),
        ("diagnostics.recoveries", -1),
        ("write_errors", [1]),
        ("failed_blocks", 1),
        ("current_write_generation", True),
    ],
)
def test_malformed_snapshots_are_never_published_as_live_state(tmp_path, field, value):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://127.0.0.1:8765", TOKEN)
                original = service.snapshot()
                remote.apply_snapshot(original)
                notifications = []
                remote.on_update = lambda: notifications.append(remote.connected)
                bad = deepcopy(original)
                parts = field.split(".")
                if len(parts) == 1:
                    bad[field] = value
                else:
                    bad[parts[0]][parts[1]] = value
                with pytest.raises(
                    AmtronConnectionError, match="invalid service snapshot"
                ):
                    remote.apply_snapshot(bad)
                assert not remote.connected
                assert notifications == [False]
                assert remote.snapshot is original
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "payload", "kind"),
    [
        (401, {}, AmtronConnectionError),
        (403, {}, AmtronConnectionError),
        (503, {}, AmtronConnectionError),
        (200, [], AmtronConnectionError),
        (409, {"error": 1}, AmtronConnectionError),
        (
            409,
            {"error": "write_failed", "placeholders": {"error": 1}},
            AmtronConnectionError,
        ),
        (409, {"error": "read_only_mode", "validation": True}, DeviceValidationError),
        (
            409,
            {"error": "write_failed", "placeholders": {"register": "current"}},
            DeviceError,
        ),
    ],
)
def test_http_errors_have_stable_types_and_no_retries(status, payload, kind):
    async def run():
        calls = []

        async def respond(_request):
            calls.append(1)
            return web.json_response(payload, status=status)

        app = web.Application()
        app.router.add_get("/v1/state", respond)
        async with TestServer(app) as server, ClientSession() as session:
            remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
            with pytest.raises(kind):
                await remote.request("GET", "/v1/state")
            assert calls == [1]

    asyncio.run(run())


def test_large_or_streamed_replies_are_bounded():
    async def run():
        async def oversized(_request):
            return web.Response(body=b"x" * 131073)

        app = web.Application()
        app.router.add_get("/v1/state", oversized)
        async with TestServer(app) as server, ClientSession() as session:
            remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
            with pytest.raises(AmtronConnectionError, match="service response too large"):
                await remote.update()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("async_set_charging_release", False),
        ("async_set_lock_evse", True),
        ("async_set_solar_charging_mode", SolarChargingMode.FAST),
        ("async_set_requested_phases", RequestedPhases.SINGLE_PHASE),
        ("async_restart", None),
        ("async_recover_from_error", None),
    ],
)
def test_every_remote_action_uses_the_shared_service(tmp_path, method, value):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote,
                    diagnostics=WriteDiagnostics(),
                    options=ControlOptions(False, 16),
                    state=lambda: WallboxData(values=remote.snapshot["values"]),
                    request_refresh=remote.update,
                )
                await getattr(control, method)(*(() if value is None else (value,)))
                assert service.control.read_only is False
                if method == "async_set_charging_release":
                    assert control.charging_release_requested is False
                if method != "async_recover_from_error":
                    assert any(
                        addr != R.HEARTBEAT.address for addr, _ in transport.writes
                    )
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("cap", [6, 16])
@pytest.mark.parametrize("unknown_target", [False, True])
def test_remote_resume_respects_the_local_current_cap(tmp_path, cap, unknown_target):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote,
                    diagnostics=WriteDiagnostics(),
                    options=ControlOptions(False, cap),
                    state=lambda: WallboxData(values=remote.snapshot["values"]),
                    request_refresh=remote.update,
                )
                await control.async_pause_charging()
                clock[0] += 6
                if unknown_target:
                    service.control.charging_setpoint = None
                    await remote.update()
                await control.async_resume_charging()
                assert current_writes(transport) == [
                    1, cap if unknown_target else min(8, cap)
                ]
                with pytest.raises(ServiceValidationError):
                    await control.async_set_charging_current(float("nan"))
                for invalid_cap in (5, float("nan")):
                    with pytest.raises(ServiceValidationError):
                        await control.async_resume_charging(current_cap=invalid_cap)
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("cap", [None, True, 5, 33, float("nan"), float("inf")])
def test_service_rejects_invalid_resume_caps_before_any_write(tmp_path, cap):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            with pytest.raises(ValueError, match="numeric value"):
                await service.command({"command": "resume", "current_cap": cap})
            assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("cached", "fresh", "cap", "expected"), [
    (6, 16, 6, 6),
    (16, 6, 16, 6),
    (6, 0, 6, 0),
])
def test_remote_resume_uses_fresh_service_intent_with_the_ha_cap(
    tmp_path, cached, fresh, cap, expected
):
    async def run():
        service, transport, clock = await service_fixture(tmp_path, current=cached)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote,
                    diagnostics=WriteDiagnostics(),
                    options=ControlOptions(False, cap),
                    state=lambda: WallboxData(values=remote.snapshot["values"]),
                    request_refresh=remote.update,
                )
                # Another authenticated client changes the service while HA's
                # last snapshot still contains the previous resume current.
                await service.command({
                    "command": "set_current", "value": fresh, "allow_unlimited": True
                })
                clock[0] += 6
                await service.command({"command": "pause"})
                clock[0] += 6
                assert control.charging_setpoint == cached
                await control.async_resume_charging()
                assert current_writes(transport) == [fresh, 1, expected]
        finally:
            await service.stop()

    asyncio.run(run())


def test_remote_diagnostics_and_raw_write_refusal(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                assert await remote.async_read_register(R.ERROR_CODE) == 0
                assert await remote.async_read_register(R.CHARGING_CURRENT_EMS) == 8
                block = next(b for b in REGISTER_BLOCKS if b.name == "status")
                assert (
                    len(
                        await remote.async_read_words(
                            address=block.address, count=block.count
                        )
                    )
                    == block.count
                )
                with pytest.raises(
                    AmtronConnectionError, match="unsupported diagnostic"
                ):
                    await remote.async_read_words(address=123, count=1)
                with pytest.raises(AmtronWriteRejectedError):
                    await remote.async_write_register(R.CHARGING_CURRENT_EMS, 16)
                await server.close()
                control = RemoteControl(
                    remote,
                    diagnostics=WriteDiagnostics(),
                    options=ControlOptions(False, 16),
                    state=WallboxData,
                    request_refresh=remote.update,
                )
                with pytest.raises(HomeAssistantError):
                    await control.async_pause_charging()
        finally:
            await service.stop()

    asyncio.run(run())


def test_write_diagnostics_endpoint_requires_auth_and_performs_no_bus_io(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            await service.heartbeat.async_stop()
            service.diagnostics.timeline.record(EventType.CONNECTION_LOST)
            before = (list(transport.reads), list(transport.writes))
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                url = server.make_url("/v1/diagnostics")
                async with session.get(url) as response:
                    assert response.status == 401
                async with session.get(url, headers={"Authorization": f"Bearer {TOKEN}"}) as response:
                    assert response.status == 200
                    assert await response.json() == {
                        "write_transactions": [],
                        "runtime_events": service.diagnostics.timeline.snapshot(),
                    }
            assert (transport.reads, transport.writes) == before
        finally:
            await service.stop()

    asyncio.run(run())


def test_optional_diagnostics_failure_does_not_mark_service_offline():
    async def run():
        app = web.Application()

        async def unavailable(_request):
            raise web.HTTPNotFound

        app.router.add_get("/v1/diagnostics", unavailable)
        async with TestServer(app) as server, ClientSession() as session:
            remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
            remote._connected = True
            notifications = []
            remote.on_update = lambda: notifications.append(True)
            with pytest.raises(AmtronConnectionError):
                await remote.request("GET", "/v1/diagnostics", update_connection=False)
            assert remote.connected
            assert notifications == []

    asyncio.run(run())


def test_wallbox_failure_is_distinguished_from_a_lost_service_connection(tmp_path):
    from custom_components.mennekes_amtron_rtu.remote import RemoteHeartbeat

    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                heartbeat = RemoteHeartbeat(remote, WriteDiagnostics())
                remote.on_update = heartbeat.update
                service._success = False
                service.heartbeat._set_healthy(False)
                with pytest.raises(
                    AmtronConnectionError, match="wallbox is unavailable"
                ):
                    await remote.update()
                assert remote.connected
                assert heartbeat.healthy is False
                await server.close()
                with pytest.raises(AmtronConnectionError, match="cannot communicate"):
                    await remote.update()
                assert not remote.connected
                assert heartbeat.healthy is None
        finally:
            await service.stop()

    asyncio.run(run())
