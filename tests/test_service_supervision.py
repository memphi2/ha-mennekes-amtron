"""A dead heartbeat must never leave a healthy-looking service behind."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from tests.fakes import raise_connection_error
from tests.test_modbus_service import service_fixture


@pytest.mark.parametrize("failure", ["exception", "cancelled", "return"])
def test_unexpected_heartbeat_exit_fails_worker_and_closes_serial(tmp_path, caplog, failure):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        worker = service.start()
        if failure == "exception":
            transport.write_exceptions[R.HEARTBEAT.address] = RuntimeError("private-error-detail")
        elif failure == "cancelled":
            service._heartbeat_task.cancel()
        else:
            service.heartbeat._stopping = True
        try:
            with pytest.raises(RuntimeError, match="Heartbeat task stopped"):
                await asyncio.wait_for(worker, 1)
            assert service.snapshot()["available"] is False
            assert not service.heartbeat.running
            assert "private-error-detail" not in caplog.text
        finally:
            with pytest.raises(RuntimeError, match="Heartbeat task stopped"):
                await service.stop()
        assert not transport.connected
        assert not any(task.get_name() == "amtron_polling" for task in asyncio.all_tasks())

    asyncio.run(run())


@pytest.mark.parametrize("read_only", [False, True])
def test_normal_bus_failures_and_read_only_do_not_stop_worker(tmp_path, read_only):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=read_only)
        worker = service.start()
        transport.write_exceptions[R.HEARTBEAT.address] = raise_connection_error()
        try:
            await asyncio.sleep(0.06)
            assert not worker.done()
            if read_only:
                assert not service.heartbeat.running
                assert service.diagnostics.heartbeats_failed == 0
            else:
                assert service.heartbeat.running
                assert service.diagnostics.heartbeats_failed > 0
                transport.write_exceptions.clear()
                await asyncio.sleep(0.06)
                assert service.diagnostics.heartbeats_sent > 0
                assert not worker.done()
        finally:
            await service.stop()
        assert not transport.connected
        assert not any(task.get_name() == "amtron_polling" for task in asyncio.all_tasks())

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["before_monitor", "after_monitor", "read_only"])
def test_unexpected_poll_failure_propagates_and_does_not_orphan_tasks(tmp_path, stage):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=stage == "read_only")
        entered, release = asyncio.Event(), asyncio.Event()

        async def broken_refresh():
            entered.set()
            await release.wait()
            raise RuntimeError("unexpected polling failure")

        service.refresh = broken_refresh
        if stage != "after_monitor":
            release.set()
        worker = service.start()
        try:
            await entered.wait()
            if stage == "after_monitor":
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                release.set()
            with pytest.raises(RuntimeError, match="unexpected polling failure"):
                await asyncio.wait_for(worker, 1)
        finally:
            with pytest.raises(RuntimeError, match="unexpected polling failure"):
                await service.stop()
        assert not service.heartbeat.running
        assert not transport.connected
        assert not any(task.get_name() == "amtron_polling" for task in asyncio.all_tasks())

    asyncio.run(run())
