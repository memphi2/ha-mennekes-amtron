"""An API command must identify its intended wallbox before any write occurs."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_modbus_service import TOKEN, current_writes, service_fixture

SERIAL = "ABC123456789"
OTHER_SERIAL = "DIFFERENT-WALLBOX"


@pytest.mark.parametrize("precondition", [{}, {"expected_serial_number": True},
    {"expected_serial_number": 1}, {"expected_serial_number": []},
    {"expected_serial_number": {}}, {"expected_serial_number": ""},
    {"expected_serial_number": "x" * 257},
])
def test_api_rejects_missing_or_invalid_identity_preconditions(tmp_path, precondition):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
                session.post(server.make_url("/v2/command"), json={
                    "command": "set_current", "value": 10, **precondition,
                }) as response,
            ):
                assert response.status == 400
                assert await response.json() == {"error": "invalid_request"}
                assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("actual", "expected", "accepted"), [
    (SERIAL, SERIAL, True),
    (None, None, True),
    (SERIAL, None, False),
    (None, SERIAL, False),
    (OTHER_SERIAL, SERIAL, False),
])
def test_null_identity_is_an_exact_match_not_a_wildcard(tmp_path, actual, expected, accepted):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        service.identity.serial_number = actual
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
                session.post(server.make_url("/v2/command"), json={
                    "command": "set_current", "value": 10,
                    "expected_serial_number": expected,
                }) as response,
            ):
                assert response.status == (200 if accepted else 409)
                data = await response.json()
                if accepted:
                    assert data["api_version"] == 2
                else:
                    assert data["error"] == "device_identity_changed"
                    assert SERIAL not in str(data)
                    assert OTHER_SERIAL not in str(data)
                assert current_writes(transport) == ([10] if accepted else [])
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("payload", [
    {"command": "set_current", "value": 10, "expected_serial_number": OTHER_SERIAL},
    {"command": "pause", "expected_serial_number": OTHER_SERIAL},
    {"command": "resume", "expected_serial_number": OTHER_SERIAL},
    {"command": "release", "value": True, "expected_serial_number": OTHER_SERIAL},
    {"command": "lock", "value": False, "expected_serial_number": OTHER_SERIAL},
    {"command": "solar", "value": 1, "expected_serial_number": OTHER_SERIAL},
    {"command": "phases", "value": 1, "expected_serial_number": OTHER_SERIAL},
    {"command": "recover", "expected_serial_number": OTHER_SERIAL},
    {"command": "restart", "expected_serial_number": OTHER_SERIAL},
])
def test_every_command_rejects_a_different_wallbox_without_bus_reads_or_intent_changes(
    tmp_path, caplog, payload
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, current=1)
        reads = list(transport.reads)
        saved = service.state_path.read_bytes()
        intent = service.snapshot()["control"]
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
                session.post(server.make_url("/v2/command"), json=payload) as response,
            ):
                assert response.status == 409
                assert (await response.json())["error"] == "device_identity_changed"
                assert transport.reads == reads
                assert all(address == R.HEARTBEAT.address for address, _ in transport.writes)
                assert service.snapshot()["control"] == intent
                assert service.state_path.read_bytes() == saved
                assert SERIAL not in caplog.text
                assert OTHER_SERIAL not in caplog.text
        finally:
            await service.stop()

    asyncio.run(run())


def test_identity_is_checked_after_waiting_for_the_service_command_lock(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        queued = asyncio.Event()

        class ObservedLock(asyncio.Lock):
            async def acquire(self):
                if self.locked():
                    queued.set()
                return await super().acquire()

        lock = ObservedLock()
        service._command_lock = lock
        await lock.acquire()
        pending = None
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
            ):
                url = server.make_url("/v2/command")
                pending = asyncio.create_task(session.post(url, json={
                    "command": "set_current", "value": 10,
                    "expected_serial_number": SERIAL,
                }))
                await asyncio.wait_for(queued.wait(), 2)
                service.identity.serial_number = OTHER_SERIAL
                lock.release()
                async with await asyncio.wait_for(pending, 2) as response:
                    assert response.status == 409
                    assert (await response.json())["error"] == "device_identity_changed"
                assert not current_writes(transport)
                service.identity.serial_number = SERIAL
                await service.refresh()
                assert not current_writes(transport)
                async with session.post(url, json={
                    "command": "set_current", "value": 12,
                    "expected_serial_number": SERIAL,
                }) as response:
                    assert response.status == 200
                assert current_writes(transport) == [12]
        finally:
            if lock.locked():
                lock.release()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/v1/state"), ("GET", "/v1/diagnostics"),
    ("POST", "/v1/read"), ("POST", "/v1/command"),
])
def test_removed_api_v1_has_no_aliases_or_write_access(tmp_path, method, path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
                session.request(method, server.make_url(path), json={
                    "command": "set_current", "value": 10,
                }) as response,
            ):
                assert response.status == 404
                assert not current_writes(transport)
        finally:
            await service.stop()

    asyncio.run(run())
