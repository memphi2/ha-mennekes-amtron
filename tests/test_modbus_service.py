"""The standalone bus owner must survive clients, validate commands and retain intent."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.device.data import (
    WallboxData,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from custom_components.mennekes_amtron_rtu.service_client import (
    ServiceClient,
    server_url,
)
from custom_components.mennekes_amtron_rtu.service_control import (
    ServiceControl,
    ServiceHeartbeat,
)
from tests.fakes import FakeModbusClient, device_bank, put, raise_connection_error
from tests.ha_fakes import heartbeat_running

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
            remote = ServiceClient(session, url, TOKEN)
            try:
                await remote.async_connect()
                data = WallboxData(values=remote.snapshot["values"])

                async def refresh():
                    nonlocal data
                    data = await remote.update()

                control = ServiceControl(
                    remote,
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
                fresh = ServiceClient(session, url, TOKEN)
                await fresh.async_connect()
                restored = ServiceControl(
                    fresh,
                    state=lambda: data,
                    request_refresh=refresh,
                )
                # The intent comes from the service, never from the client.
                for name in ("pause_requested", "charging_setpoint", "charging_release_requested"):
                    with pytest.raises(AttributeError):
                        setattr(restored, name, None)
                assert restored.pause_requested is paused
                assert restored.charging_setpoint == (8 if paused else 10)
                assert current_writes(transport) == accepted
                assert service.heartbeat.running
                heartbeat = ServiceHeartbeat(fresh)
                assert heartbeat.healthy is True
                assert heartbeat_running(heartbeat)
                await fresh.async_close()
                heartbeat.update()
                assert heartbeat.healthy is None
                assert not heartbeat_running(heartbeat)
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
                # A header that is no text is wrong, not a server error.
                reader, writer = await asyncio.open_connection(server.host, server.port)
                writer.write(b"GET /v2/state HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer \xff\xfe\r\n\r\n")
                assert (await reader.readline()).split()[1] == b"401"
                writer.close()
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
                # A probe that asks with HEAD gets the answer of GET, without a body.
                async with session.head(server.make_url("/v2/state"), headers=headers) as response:
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
        ("phases", 1, R.REQUESTED_PHASES, 1),
        ("restart", None, R.SYSTEM_RESTART, 0xBB),
    ],
)
def test_service_uses_the_shared_command_guards(
    tmp_path, command, value, register, expected
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        put(transport.bank, R.CP_STATE, int(R.CpState.A1))
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
        {"token": "x" * 31 + "\x00"},
        {"token": "x" * 31 + "\x1b"},
        {"token": "x" * 31 + "\x7f"},
        {"token": "x" * 8184},
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


@pytest.mark.parametrize("size", [32, 8183])
def test_service_token_accepts_valid_header_boundaries(size):
    token = "!" + "x" * (size - 2) + "~"
    config = ServerConfig.from_dict({"serial_port": "/dev/fake", "token": token})
    assert config.token == token


def test_service_config_defaults_and_credential_redaction():
    config = ServerConfig.from_dict({"serial_port": "/dev/fake", "token": TOKEN})
    assert config.read_only is True
    assert config.current_cap == 6
    assert TOKEN not in repr(config)


@pytest.mark.parametrize(
    "content",
    [
        b"[]", b"", b"{", b"\xff\xfe",
        json.dumps({"version": 2, "identity": "wallbox"}).encode(),
        json.dumps({"version": 1, "identity": "other"}).encode(),
        json.dumps({"version": 1, "identity": "wallbox", "pause_requested": 1}).encode(),
        json.dumps({"version": 1, "identity": "wallbox", "charging_setpoint": 3}).encode(),
        b'{"version":1,"identity":"wallbox","charging_setpoint":1' + b"0" * 400 + b"}",
        b"[" * 100000 + b"]" * 100000,
    ],
)
def test_a_rejected_saved_state_counts_as_none(tmp_path, content):
    path = tmp_path / "intent.json"
    path.write_bytes(content)
    state = WriteState(pause_requested=True, charging_setpoint=8)
    assert IntentStore(path, "wallbox").load(state) is False
    # Nothing of a rejected file applies.
    assert (state.pause_requested, state.charging_setpoint) == (True, 8)


def test_a_rejected_saved_state_does_not_keep_the_service_down(tmp_path, caplog):
    async def run():
        path = tmp_path / "intent.json"
        path.write_text(json.dumps({"version": 1, "identity": "another-wallbox", "pause_requested": True}))
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            assert service.control is not None
            assert service.client.write_state.pause_requested is True
            assert service.control.read_only
            assert caplog.text.count("Control: saved intent rejected") == 1
            # Rejected evidence survives until an explicit acknowledgement.
            assert json.loads(path.read_text())["identity"] == "another-wallbox"
            # Polls and reconnects neither call it solved nor report it again.
            caplog.set_level("DEBUG")
            await service.refresh()
            await service._initialize()
            assert "intent saved again" not in caplog.text
            warnings = [
                r for r in caplog.records
                if "saved intent rejected" in r.getMessage() and r.levelname == "WARNING"
            ]
            assert len(warnings) == 1
            await service.command({
                "command": "acknowledge_intent_reset",
                "expected_serial_number": service.identity.serial_number,
            })
            assert "Control: saved intent reset acknowledged" in caplog.text
        finally:
            await service.stop()
    asyncio.run(run())


def test_a_failing_disk_does_not_block_the_start(tmp_path, monkeypatch, caplog):
    async def run():
        def fail(_self, _state):
            raise OSError("read-only file system")

        monkeypatch.setattr(IntentStore, "save", fail)
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            assert service.control is not None
            assert "Control: intent not saved" in caplog.text
        finally:
            monkeypatch.undo()
            await service.stop()
    asyncio.run(run())


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


def test_one_lost_poll_does_not_pause_a_kwh_goal(tmp_path, monkeypatch):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            serial = service.identity.serial_number
            await service.command({"command": "energy_goal", "value": 5.0, "expected_serial_number": serial})
            assert service.control.energy_goal.status == "active"
            # The whole poll fails, not only the meter block.
            transport.read_exceptions[R.EVSE_STATE.address] = raise_connection_error()
            failing = [1000.0]
            monkeypatch.setattr(service_runtime.time, "monotonic", lambda: failing[0])
            with pytest.raises(AmtronBusError):
                await service.refresh()
            assert service.control.energy_goal.status == "active"
            assert service._energy_sample is None
            # After a minute of failures the goal is told about the outage.
            failing[0] += 61
            with pytest.raises(AmtronBusError):
                await service.refresh()
            for _ in range(20):
                await asyncio.sleep(0)
            assert service.control.energy_goal.status == "meter_unavailable"
            transport.read_exceptions.clear()
            await service.refresh()
            assert service._meter_failing_since is None
        finally:
            await service.stop()
    asyncio.run(run())


def test_a_failing_intent_save_keeps_the_wallbox_available(tmp_path, monkeypatch, caplog):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async def fail(**_kwargs):
                raise OSError("disk full")

            monkeypatch.setattr(service, "_persist", fail)
            await service._save_intent()
            await service._save_intent()
            assert caplog.text.count("Control: intent not saved") == 1
            assert service._is_available()
            monkeypatch.undo()
        finally:
            await service.stop()
    asyncio.run(run())


def test_a_command_polls_the_wallbox_once_after_its_write(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        try:
            clock[0] += 100
            first = len(transport.reads)
            await service.command({"command": "set_current", "value": 10, "expected_serial_number": "ABC123456789"})
            # The serial check and one poll that reads the write back.
            after = transport.reads[first:]
            assert after.count(after[-1]) == 1
            assert current_writes(transport)[-1] == 10
            # A command without a write still polls for its answer.
            first = len(transport.reads)
            await service.command({"command": "set_current", "value": 10, "expected_serial_number": "ABC123456789"})
            assert len(transport.reads) > first
        finally:
            await service.stop()

    asyncio.run(run())


def test_a_command_reads_its_own_write_back_beside_a_queued_command(tmp_path):
    """The poll after a queued command's write is not this command's read-back."""
    from custom_components.mennekes_amtron_rtu.device.enums import EvseState
    from tests.test_plug_charge_core import POLICY, deadline

    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        serial = service.identity.serial_number
        try:
            transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
            await service.command({
                "command": "plug_charge_config", "value": True, "policy": POLICY, "expected_serial_number": serial,
            })
            session = service.control.plug_charge["session"]
            clock[0] += 301
            wake, entered, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()

            async def sleep(_delay):
                await wake.wait()

            service.control._sleep = sleep
            for value in (10, 12):
                await service.command({"command": "set_current", "value": value, "expected_serial_number": serial})
            refresh = service.refresh

            async def slow_refresh():
                if not entered.is_set():
                    entered.set()
                    await gate.wait()
                await refresh()

            service.refresh = slow_refresh
            clock[0] += 30
            wake.set()
            await asyncio.wait_for(entered.wait(), 5)
            # The queued current is written and its poll is under way.
            authorize = asyncio.create_task(service.command({
                "command": "plug_charge_authorize", "session": session,
                "deadline": deadline().isoformat(), "expected_serial_number": serial,
            }))
            await asyncio.sleep(0.05)
            gate.set()
            result = await authorize
            assert result["control"]["plug_charge"]["authorized"] is True
            assert result["values"]["charging_release"] == 1
        finally:
            await service.stop()

    asyncio.run(run())
