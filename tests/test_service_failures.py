"""Service recovery, storage and lifecycle faults must remain bounded."""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import SerialConfig
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from tests.fakes import put, raise_connection_error
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


@pytest.mark.parametrize("endpoint", ["command", "read"])
def test_api_bus_queue_is_bounded_without_blocking_heartbeat(tmp_path, endpoint):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        release = asyncio.Event()
        saturated = asyncio.Event()
        waiting = 0

        async def slow_operation(_payload):
            nonlocal waiting
            waiting += 1
            if waiting == 8:
                saturated.set()
            await release.wait()
            return {}

        setattr(service, endpoint, slow_operation)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
            ):
                url = server.make_url(f"/v1/{endpoint}")
                pending = [
                    asyncio.create_task(session.post(url, json={})) for _ in range(8)
                ]
                try:
                    await asyncio.wait_for(saturated.wait(), 2)
                    async with session.post(url, json={}) as response:
                        assert response.status == 503
                    async with session.get(server.make_url("/v1/state")) as response:
                        assert response.status == 200
                    before = service.diagnostics.heartbeats_sent
                    await asyncio.sleep(0.08)
                    assert service.diagnostics.heartbeats_sent > before
                finally:
                    release.set()
                    for response in await asyncio.gather(*pending):
                        response.release()
                async with session.post(url, json={}) as response:
                    assert response.status == 200
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["persistent", "read", "write", "cooldown", "changed_error"]
)
def test_service_recovery_is_bounded_and_never_lifts_a_pause(tmp_path, failure):
    async def run():
        service, transport, clock = await service_fixture(tmp_path, current=1)
        try:
            put(transport.bank, R.ERROR_CODE, 200)
            await service.refresh()
            service.heartbeat._set_healthy(True)
            service.control.pause_requested = True
            if failure == "read":
                transport.read_exceptions[R.ERROR_CODE.address] = (
                    raise_connection_error()
                )
            elif failure == "write":
                transport.write_exceptions[R.CHARGING_RELEASE.address] = (
                    raise_connection_error()
                )
            elif failure == "cooldown":
                service.client.write_state.mode_change_at = clock[0]
            elif failure == "changed_error":
                put(transport.bank, R.ERROR_CODE, 2011)
            await service._recover()
            if failure == "cooldown":
                assert service._attempts == 0
                assert not current_writes(transport)
                clock[0] += 6
            else:
                assert service._attempts == 1
            for _ in range(4):
                service._retry_at = 0
                clock[0] += 6
                await service._recover()
            assert service._attempts == 3
            assert all(value == 1 for value in current_writes(transport))
            transport.read_exceptions.clear()
            transport.write_exceptions.clear()
            put(transport.bank, R.ERROR_CODE, 0)
            await service.refresh()
            await service._recover()
            assert service._attempts == 0
        finally:
            await service.stop()

    asyncio.run(run())


def test_service_retries_initial_connection_and_preserves_live_tasks(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            transport.read_exceptions[R.EVSE_STATE.address] = raise_connection_error()
            with pytest.raises(AmtronConnectionError):
                await service.refresh()
            assert service.snapshot()["available"] is False
            service.start()
            await asyncio.sleep(0.02)
            assert service.heartbeat.running
            assert service._task is not None
            assert not service._task.done()
        finally:
            await service.stop()
        unready = ModbusService(
            ServerConfig(SerialConfig("/dev/nonexistent-amtron"), TOKEN),
            tmp_path / "other.json",
        )
        assert unready.snapshot()["available"] is False
        with pytest.raises(DeviceError):
            await unready.refresh()
        with pytest.raises(DeviceError):
            await unready.command({"command": "pause"})
        unready._initialize = AsyncMock(side_effect=AmtronConnectionError("no port"))
        unready.start()
        await asyncio.sleep(0.02)
        assert unready._logged_unavailable
        await unready.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "set_current", "value": 8, "allow_unlimited": 1},
        {"command": "release", "value": 1},
        {"command": "lock", "value": "false"},
    ],
)
def test_malformed_command_booleans_are_not_coerced(tmp_path, payload):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            with pytest.raises(ValueError, match="boolean"):
                await service.command(payload)
            assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


def test_persist_finishes_atomically_even_when_the_caller_is_cancelled(
    tmp_path, monkeypatch
):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            entered = threading.Event()
            release = threading.Event()
            save = service._store.save

            def blocked(state):
                entered.set()
                assert release.wait(5)
                save(state)

            monkeypatch.setattr(service._store, "save", blocked)
            service.control.pause_requested = True
            task = asyncio.create_task(service._persist())
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (
                json.loads((tmp_path / "intent.json").read_text())["pause_requested"]
                is True
            )
            monkeypatch.setattr(service._store, "save", save)
        finally:
            await service.stop()

    asyncio.run(run())


def test_io_failure_is_reported_without_leaking_exception_details(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            transport.read_exceptions[R.ERROR_CODE.address] = raise_connection_error()
            async with (
                TestServer(create_app(service)) as server,
                ClientSession() as session,
                session.post(
                    server.make_url("/v1/read"),
                    headers={"Authorization": f"Bearer {TOKEN}"},
                    json={"register": "error_code"},
                ) as response,
            ):
                assert response.status == 503
                assert await response.json() == {"error": "service_unavailable"}
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(
    "content",
    [None, "{", "[]", '{"serial_port":"/dev/fake","token":"private-invalid"}'],
)
def test_service_cli_rejects_invalid_configuration_without_echoing_secrets(
    tmp_path, monkeypatch, capsys, content
):
    from custom_components.mennekes_amtron_rtu.device import server

    path = tmp_path / "options.json"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr("sys.argv", ["amtron-modbus-service", "--config", str(path)])
    with pytest.raises(SystemExit) as exit_info:
        server.main()
    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "Invalid or unreadable" in error
    assert "private-invalid" not in error


def test_service_shutdown_releases_the_port_even_if_state_cannot_be_saved(
    tmp_path, monkeypatch
):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        monkeypatch.setattr(
            service, "_persist", AsyncMock(side_effect=OSError("disk full"))
        )
        with pytest.raises(OSError, match="disk full"):
            await service.stop()
        assert not service.client.connected
        assert not service.heartbeat.running

    asyncio.run(run())


def test_unexpected_worker_exit_stops_heartbeat_serial_and_discovery(
    tmp_path, monkeypatch, caplog
):
    from dataclasses import replace

    from custom_components.mennekes_amtron_rtu.device import server

    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        cancelled = asyncio.Event()

        async def failed_worker():
            await asyncio.sleep(0.01)
            raise RuntimeError("unexpected worker failure")

        async def pending_discovery(*_args):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        monkeypatch.setattr(service, "_run", failed_worker)
        monkeypatch.setattr(server, "ModbusService", lambda *_: service)
        monkeypatch.setattr(server, "publish_discovery", pending_discovery)
        with pytest.raises(RuntimeError, match="unexpected worker failure"):
            await asyncio.wait_for(
                server.run(
                    replace(service.config, port=0),
                    tmp_path / "intent.json",
                    "supervisor-private",
                ),
                2,
            )
        assert cancelled.is_set()
        assert not service.client.connected
        assert not service.heartbeat.running
        assert "Modbus worker stopped unexpectedly" in caplog.text

    asyncio.run(run())


def test_fatal_cli_error_is_actionable_and_does_not_echo_private_details(
    tmp_path, monkeypatch, caplog
):
    from custom_components.mennekes_amtron_rtu.device import server

    path = tmp_path / "options.json"
    path.write_text(
        json.dumps({"serial_port": "/dev/fake", "token": TOKEN, "baudrate": "57600"})
    )
    monkeypatch.setattr("sys.argv", ["amtron-modbus-service", "--config", str(path)])

    async def fail(*_args):
        raise OSError(98, f"sensitive bind error {TOKEN}")

    monkeypatch.setattr(server, "run", fail)
    with pytest.raises(SystemExit) as error:
        server.main()
    assert error.value.code == 1
    assert "Modbus service terminated (OSError, errno=98)" in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize(
    ("key", "value"), [("baudrate", "bad"), ("device_id", 51), ("current_cap", 100)]
)
def test_cli_configuration_error_names_the_invalid_field(
    tmp_path, monkeypatch, capsys, key, value
):
    from custom_components.mennekes_amtron_rtu.device import server

    path = tmp_path / "options.json"
    path.write_text(
        json.dumps({"serial_port": "/dev/fake", "token": TOKEN, key: value})
    )
    monkeypatch.setattr("sys.argv", ["amtron-modbus-service", "--config", str(path)])
    with pytest.raises(SystemExit) as error:
        server.main()
    assert error.value.code == 2
    log = capsys.readouterr().err
    assert f"validating options: {key}:" in log
    assert TOKEN not in log
