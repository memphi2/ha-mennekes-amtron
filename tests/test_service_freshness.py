"""The API must not act on wallbox data that it already reports as unavailable."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "set_current", "value": 10, "expected_serial_number": "ABC123456789"},
        {"command": "pause", "expected_serial_number": "ABC123456789"},
        {"command": "resume", "expected_serial_number": "ABC123456789"},
        {"command": "release", "value": True, "expected_serial_number": "ABC123456789"},
        {"command": "lock", "value": False, "expected_serial_number": "ABC123456789"},
        {"command": "solar", "value": 1, "expected_serial_number": "ABC123456789"},
        {"command": "phases", "value": 1, "expected_serial_number": "ABC123456789"},
        {"command": "recover", "expected_serial_number": "ABC123456789"},
        {"command": "restart", "expected_serial_number": "ABC123456789"},
    ],
)
def test_stale_service_rejects_every_command_without_changing_intent(tmp_path, payload):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, current=1)
        saved = service.state_path.read_bytes()
        intent = service.snapshot()["control"]
        service._updated_at -= 3600
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
            ):
                async with session.get(server.make_url("/v2/state")) as response:
                    assert (await response.json())["available"] is False
                async with session.post(
                    server.make_url("/v2/command"), json=payload
                ) as response:
                    assert response.status == 409
                    assert (await response.json())["error"] == "service_unavailable"
                assert service.snapshot()["control"] == intent
                assert service.state_path.read_bytes() == saved
                assert all(address == R.HEARTBEAT.address for address, _ in transport.writes)
                before = service.diagnostics.heartbeats_sent
                await asyncio.sleep(0.08)
                assert service.diagnostics.heartbeats_sent > before
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("scan_interval", [5, 20])
@pytest.mark.parametrize("offset", [-0.01, 0, 0.01])
def test_snapshot_and_command_use_the_same_freshness_boundary(
    tmp_path, monkeypatch, scan_interval, offset
):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        now = service._updated_at + max(30, scan_interval * 3) + offset
        monkeypatch.setattr(service_runtime, "time", SimpleNamespace(monotonic=lambda: now))
        service.config = replace(service.config, scan_interval=scan_interval)
        available = offset <= 0
        try:
            async with (
                TestServer(create_app(service)) as server,
                ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as session,
            ):
                async with session.get(server.make_url("/v2/state")) as response:
                    assert (await response.json())["available"] is available
                async with session.post(
                    server.make_url("/v2/command"),
                    json={"command": "set_current", "value": 10, "expected_serial_number": "ABC123456789"},
                ) as response:
                    assert response.status == (200 if available else 409)
                assert current_writes(transport) == ([10] if available else [])
        finally:
            await service.stop()

    asyncio.run(run())


def test_command_rechecks_freshness_after_waiting_for_another_command(tmp_path):
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
                assert service.snapshot()["available"] is True
                url = server.make_url("/v2/command")
                pending = asyncio.create_task(
                    session.post(url, json={"command": "set_current", "value": 10, "expected_serial_number": "ABC123456789"})
                )
                await asyncio.wait_for(queued.wait(), 2)
                service._updated_at -= 3600
                lock.release()
                async with await asyncio.wait_for(pending, 2) as response:
                    assert response.status == 409
                    assert (await response.json())["error"] == "service_unavailable"
                assert not current_writes(transport)
                await service.refresh()
                assert service.snapshot()["available"] is True
                assert not current_writes(transport)
                async with session.post(
                    url, json={"command": "set_current", "value": 12, "expected_serial_number": "ABC123456789"}
                ) as response:
                    assert response.status == 200
                assert current_writes(transport) == [12]
        finally:
            if lock.locked():
                lock.release()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await service.stop()

    asyncio.run(run())
