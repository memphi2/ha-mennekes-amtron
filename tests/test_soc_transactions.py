"""An interrupted target request must be ordered before a safe resolution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageStateUnknownError,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.test_modbus_service import TOKEN, service_fixture

SOURCE = "sensor.car|"


def _target(service, token, value=80):
    return {
        "token": token, "expected_serial_number": service.identity.serial_number,
        "source": SOURCE, "value": value, "soc": 50, "maximum": None,
    }


async def _ready(tmp_path):
    service, _transport, _clock = await service_fixture(tmp_path)
    await service.refresh()
    await service.command({
        "command": "soc_source", "source": SOURCE,
        "expected_serial_number": service.identity.serial_number,
    })
    return service


def test_fenced_unstarted_commit_cannot_run_late(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        try:
            token = service.soc_transactions.reserve()
            assert (await service.soc_transactions.resolve(token))["outcome"] == "cancelled"
            assert (await service.soc_transactions.commit(_target(service, token)))["outcome"] == "cancelled"
            assert service.control.soc_goal.target_percent == 0
        finally:
            await service.stop()

    asyncio.run(run())


def test_lost_commit_response_resolves_after_persistence_without_replay(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()
        original = service.soc_goal._persist

        async def slow_persist():
            started.set()
            await release.wait()
            await original()

        service.soc_goal._persist = slow_persist
        token = service.soc_transactions.reserve()
        gate = asyncio.Semaphore(1)
        commit = asyncio.create_task(service.soc_transactions.commit(_target(service, token), gate=gate))
        try:
            await started.wait()
            assert gate.locked()
            commit.cancel()
            with pytest.raises(asyncio.CancelledError):
                await commit
            assert gate.locked()
            resolving = asyncio.create_task(service.soc_transactions.resolve(token))
            await asyncio.sleep(0)
            assert not resolving.done()
            release.set()
            result = await resolving
            assert not gate.locked()
            assert result["outcome"] == "applied"
            assert result["revision"] == result["snapshot"]["control"]["soc_goal_revision"]
            assert result["snapshot"]["control"]["soc_goal"]["target_percent"] == 80
            assert (await service.soc_transactions.commit(_target(service, token, 90)))["revision"] == result["revision"]
            assert service.control.soc_goal.target_percent == 80
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())


def test_observation_revision_fences_later_goal(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        try:
            token = service.soc_transactions.reserve()
            result = await service.soc_transactions.commit(_target(service, token))
            revision = result["revision"]
            await service.command({
                "command": "soc_goal", "expected_serial_number": service.identity.serial_number,
                "value": 90, "source": SOURCE, "soc": 50,
            })
            with pytest.raises(DeviceError, match="soc_goal_source_changed"):
                await service.command({
                    "command": "soc_observation", "expected_serial_number": service.identity.serial_number,
                    "source": SOURCE, "soc": 95, "expected_soc_revision": revision,
                })
            assert service.control.soc_goal.status == "active"
        finally:
            await service.stop()

    asyncio.run(run())


def test_reservations_are_bounded_and_old_boot_tokens_are_unknown(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        replacement = None
        try:
            tokens = [service.soc_transactions.reserve() for _ in range(64)]
            with pytest.raises(ValueError, match="too many"):
                service.soc_transactions.reserve()
            old_revision = service.snapshot()["control"]["soc_goal_revision"]
            replacement, _transport, _clock = await service_fixture(tmp_path)
            assert replacement.snapshot()["control"]["soc_goal_revision"] != old_revision
            assert (await replacement.soc_transactions.resolve(tokens[0]))["outcome"] == "unknown"
            assert (await replacement.soc_transactions.commit(_target(replacement, tokens[0])))["outcome"] == "unknown"
        finally:
            if replacement is not None:
                await replacement.stop()
            await service.stop()

    asyncio.run(run())


def test_expired_unstarted_token_cannot_apply_a_late_commit(tmp_path, monkeypatch):
    async def run():
        service = await _ready(tmp_path)
        try:
            monkeypatch.setattr(
                "custom_components.mennekes_amtron_rtu.device.soc_transactions._LIFETIME", -1,
            )
            token = service.soc_transactions.reserve()
            assert (await service.soc_transactions.commit(_target(service, token)))["outcome"] == "unknown"
            assert service.control.soc_goal.target_percent == 0
        finally:
            monkeypatch.undo()
            await service.stop()

    asyncio.run(run())


def test_definite_invalid_target_is_rejected_but_unknown_storage_is_not(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        try:
            token = service.soc_transactions.reserve()
            result = await service.soc_transactions.commit(_target(service, token, 101))
            assert result["outcome"] == "rejected"
            assert service.control.soc_goal.target_percent == 0

            async def unknown():
                raise StorageStateUnknownError("readback failed")

            service.soc_goal._persist = unknown
            token = service.soc_transactions.reserve()
            result = await service.soc_transactions.commit(_target(service, token))
            assert result["outcome"] == "unknown"
        finally:
            await service.stop()

    asyncio.run(run())


def test_http_reservation_is_authenticated_and_late_commit_is_fenced(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as client:
                url = str(server.make_url("/v2/soc-goal/reserve"))
                async with client.post(url, json={}) as response:
                    assert response.status == 401
                headers = {"Authorization": f"Bearer {TOKEN}"}
                async with client.post(url, json={}, headers=headers) as response:
                    assert response.status == 200
                    token = (await response.json())["token"]
                async with client.post(
                    server.make_url("/v2/soc-goal/resolve"), json={"token": token}, headers=headers,
                ) as response:
                    assert (await response.json())["outcome"] == "cancelled"
                async with client.post(
                    server.make_url("/v2/soc-goal/commit"), json=_target(service, token), headers=headers,
                ) as response:
                    assert (await response.json())["outcome"] == "cancelled"
                assert service.control.soc_goal.target_percent == 0
        finally:
            await service.stop()

    asyncio.run(run())


def test_recovery_after_refresh_failure_reports_saved_goal(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        try:
            service.refresh = AsyncMock(side_effect=OSError("refresh failed"))
            token = service.soc_transactions.reserve()
            result = await service.soc_transactions.commit(_target(service, token))
            assert result["outcome"] == "applied"
            assert result["snapshot"]["control"]["soc_goal"]["target_percent"] == 80
        finally:
            await service.stop()

    asyncio.run(run())


def test_invalid_reservation_and_early_identity_failure(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        try:
            with pytest.raises(ValueError, match="reservation"):
                await service.soc_transactions.resolve("not-a-token")
            with pytest.raises(ValueError, match="request"):
                await service.soc_transactions.commit({})
            token = service.soc_transactions.reserve()
            payload = _target(service, token)
            payload["expected_serial_number"] = "different"
            assert (await service.soc_transactions.commit(payload))["outcome"] == "rejected"
        finally:
            await service.stop()
        with pytest.raises(ValueError, match="stopping"):
            service.soc_transactions.reserve()

    asyncio.run(run())


def test_state_waits_for_goal_save_and_never_exposes_rolled_back_candidate(tmp_path):
    async def run():
        service = await _ready(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()
        try:
            await service.command({
                "command": "soc_goal", "expected_serial_number": service.identity.serial_number,
                "value": 70, "source": SOURCE, "soc": 50,
            })
            revision = service.snapshot()["control"]["soc_goal_revision"]

            async def failing_save():
                started.set()
                await release.wait()
                raise OSError("disk full")

            service.soc_goal._persist = failing_save
            async with TestServer(create_app(service)) as server, ClientSession() as client:
                task = asyncio.create_task(service.command({
                    "command": "soc_goal", "expected_serial_number": service.identity.serial_number,
                    "value": 90, "source": SOURCE, "soc": 50,
                }))
                await started.wait()
                assert service.control.soc_goal.target_percent == 90
                get = asyncio.create_task(client.get(
                    server.make_url("/v2/state"), headers={"Authorization": f"Bearer {TOKEN}"},
                ))
                await asyncio.sleep(0.05)
                assert not get.done()
                release.set()
                with pytest.raises(OSError, match="disk full"):
                    await task
                async with await get as response:
                    snapshot = await response.json()
                assert snapshot["control"]["soc_goal"]["target_percent"] == 70
                assert snapshot["control"]["soc_goal_revision"] == revision
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())
