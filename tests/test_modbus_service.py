"""The standalone bus owner must survive clients, validate commands and retain intent."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.data import (
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from custom_components.mennekes_amtron_rtu.remote import (
    RemoteClient,
    RemoteControl,
    RemoteHeartbeat,
    server_url,
)
from tests.fakes import FakeModbusClient, device_bank, put

TOKEN = "service-test-credential-32-characters"  # noqa: S105 - test fixture


async def service_fixture(tmp_path, *, read_only=False, current=8):
    transport = FakeModbusClient(device_bank(charging_current=current))
    clock = [1000.0]
    client = MennekesModbusClient(
        SerialConfig("/dev/fake"),
        client_factory=lambda _: transport,
        monotonic=lambda: clock[0],
    )
    config = ServerConfig(client.config, TOKEN, read_only=read_only, current_cap=16)
    service = ModbusService(config, tmp_path / "intent.json", client=client)
    service.heartbeat._interval = 0.02
    service.heartbeat._deadline = 0.2
    await service._initialize()
    service.control._monotonic = lambda: clock[0]
    return service, transport, clock


def current_writes(transport):
    return [
        decode_register(R.CHARGING_CURRENT_EMS, words)
        for address, words in transport.writes
        if address == R.CHARGING_CURRENT_EMS.address
    ]


@pytest.mark.parametrize("paused", [False, True])
def test_client_disconnect_reconnect_never_stops_heartbeat_or_replays_commands(
    tmp_path, paused
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        async with (
            TestServer(create_app(service)) as server,
            ClientSession() as session,
        ):
            url = str(server.make_url("/"))
            remote = RemoteClient(session, url, TOKEN)
            try:
                await remote.async_connect()
                data = WallboxData(values=remote.snapshot["values"])

                async def refresh():
                    nonlocal data
                    data = await remote.update()

                control = RemoteControl(
                    remote,
                    diagnostics=WriteDiagnostics(),
                    options=ControlOptions(False, 16),
                    state=lambda: data,
                    request_refresh=refresh,
                )
                if paused:
                    await control.async_pause_charging()
                else:
                    await control.async_set_charging_current(10)
                accepted = current_writes(transport)
                before = service.diagnostics.heartbeats_sent
                await remote.async_close()
                # No HA client is connected; the service still owns the bus.
                await asyncio.sleep(0.08)
                assert service.diagnostics.heartbeats_sent > before
                assert current_writes(transport) == accepted
                fresh = RemoteClient(session, url, TOKEN)
                await fresh.async_connect()
                restored = RemoteControl(
                    fresh,
                    diagnostics=WriteDiagnostics(),
                    options=ControlOptions(False, 16),
                    state=lambda: data,
                    request_refresh=refresh,
                )
                restored.pause_requested = not paused
                restored.charging_setpoint = 32
                restored.charging_release_requested = False
                assert restored.pause_requested is paused
                assert restored.charging_setpoint == (8 if paused else 10)
                assert current_writes(transport) == accepted
                assert service.heartbeat.running
                heartbeat = RemoteHeartbeat(fresh, WriteDiagnostics())
                assert heartbeat.healthy is True
                assert heartbeat.running
                await fresh.async_close()
                heartbeat.update()
                assert heartbeat.healthy is None
                assert not heartbeat.running
            finally:
                await service.stop()

    asyncio.run(run())


def test_authentication_and_read_allowlist_prevent_unapproved_writes(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
            ):
                async with session.get(server.make_url("/v2/state")) as response:
                    assert response.status == 401
                headers = {"Authorization": f"Bearer {TOKEN}"}
                for payload in (
                    {"address": 0x302, "value": 16},
                    {"register": "charging_current_ems"},
                    {"block": "serial_number"},
                    {},
                ):
                    async with session.post(
                        server.make_url("/v2/read"), headers=headers, json=payload
                    ) as response:
                        assert response.status == 400
                async with session.post(
                    server.make_url("/v2/command"),
                    headers=headers,
                    json={"command": "resume", "expected_serial_number": "ABC123456789"},
                ) as response:
                    assert response.status == 409
                    assert (await response.json())["error"] == "read_only_mode"
                assert transport.writes == []
                async with session.get(server.make_url("/health")) as response:
                    assert response.status == 200
                for payload in (
                    [],
                    {"command": "write_register", "expected_serial_number": "ABC123456789"},
                    {"command": "pause", "address": 1, "expected_serial_number": "ABC123456789"},
                ):
                    async with session.post(
                        server.make_url("/v2/command"), headers=headers, json=payload
                    ) as response:
                        assert response.status == 400
                async with session.post(
                    server.make_url("/v2/command"), headers=headers, data="not JSON"
                ) as response:
                    assert response.status == 415
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("command", "value", "register", "expected"),
    [
        ("set_current", 0, R.CHARGING_CURRENT_EMS, 0),
        ("set_current", 32, R.CHARGING_CURRENT_EMS, 16),
        ("release", False, R.CHARGING_RELEASE, 0),
        ("lock", True, R.LOCK_EVSE, 1),
        ("solar", 2, R.SOLAR_CHARGING_MODE, 2),
        ("phases", 1, R.REQUESTED_PHASES, 1),
        ("restart", None, R.SYSTEM_RESTART, 0xBB),
    ],
)
def test_service_uses_the_shared_command_guards(
    tmp_path, command, value, register, expected
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            await service.command(
                {
                    "command": command, "value": value, "allow_unlimited": True,
                    "expected_serial_number": "ABC123456789",
                }
            )
            writes = [
                decode_register(register, words)
                for address, words in transport.writes
                if address == register.address
            ]
            assert writes == [expected]
            if command == "release":
                assert (
                    json.loads((tmp_path / "intent.json").read_text())[
                        "charging_release_requested"
                    ]
                    is False
                )
        finally:
            await service.stop()

    asyncio.run(run())


def test_service_restart_restores_pause_and_resume_current(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        await service.command({"command": "pause", "expected_serial_number": "ABC123456789"})
        await service.stop()
        # The wallbox reports a reset value, but saved pause intent wins.
        put(transport.bank, R.ERROR_CODE, 200)
        put(transport.bank, R.CHARGING_CURRENT_EMS, 0)
        replacement = ModbusService(
            service.config, tmp_path / "intent.json", client=service.client
        )
        try:
            await replacement._initialize()
            clock[0] += 6
            replacement.control._monotonic = lambda: clock[0]
            await replacement.command({"command": "recover", "expected_serial_number": "ABC123456789"})
            assert current_writes(transport) == [1, 1]
            assert replacement.control.charging_setpoint == 8
            assert replacement.control.pause_requested is True
            clock[0] += 6
            await replacement.command({"command": "resume", "expected_serial_number": "ABC123456789"})
            assert current_writes(transport) == [1, 1]
            assert replacement.control.pending_action == "resume"
            clock[0] += 300
            replacement.control._pending_changed.set()
            await asyncio.wait_for(replacement.control._flush_task, 1)
            assert current_writes(transport) == [1, 1, 8]
        finally:
            await replacement.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"serial_port": "socket://host:1234"},
        {"serial_port": "/invalid/port"},
        {"token": "short"},
        {"token": "x" * 31 + " "},
        {"token": "ü" * 32},
        {"read_only": "false"},
        {"baudrate": 12345},
        {"device_id": 51},
        {"frame": "8n1"},
        {"listen": ""},
        {"current_cap": float("nan")},
        {"scan_interval": 0},
        {"port": True},
        {"unknown": 1},
    ],
)
def test_service_config_rejects_unsafe_or_malformed_options(values):
    base = {"serial_port": "/dev/fake", "token": TOKEN}
    if values:
        base.update(values)
    else:
        base = {}
    with pytest.raises(ValueError, match=r"."):
        ServerConfig.from_dict(base)


def test_service_config_defaults_and_credential_redaction():
    config = ServerConfig.from_dict({"serial_port": "/dev/fake", "token": TOKEN})
    assert config.read_only is True
    assert config.current_cap == 6
    assert TOKEN not in repr(config)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 2, "identity": "wallbox"},
        {"version": 1, "identity": "other"},
        {"version": 1, "identity": "wallbox", "pause_requested": 1},
        {"version": 1, "identity": "wallbox", "charging_setpoint": 3},
    ],
)
def test_saved_state_is_validated_before_control_starts(tmp_path, payload):
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=r"."):
        IntentStore(path, "wallbox").load(WriteState())


def test_intent_storage_is_bounded_private_and_does_not_write_unchanged_state(tmp_path):
    path = tmp_path / "state" / "intent.json"
    store = IntentStore(path, "wallbox")
    state = WriteState(pause_requested=True, charging_setpoint=8)
    store.load(state)
    store.save(state)
    before = path.stat()
    store.save(state)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_size < 1024
    restored = WriteState()
    IntentStore(path, "wallbox").load(restored)
    assert restored.pause_requested is True
    assert restored.charging_setpoint == 8
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost",
        "http://user:password@localhost",
        "http://localhost/path",
        "http://localhost?token=x",
        "http://localhost/#x",
        "http://localhost:99999",
        "not a URL",
    ],
)
def test_service_url_has_no_embedded_credentials_or_ambiguous_paths(url):
    with pytest.raises(ValueError, match=r"."):
        server_url(url)
