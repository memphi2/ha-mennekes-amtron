"""A long command must not make the state poll time out."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.departure_plan import DepartureInputs
from tests import service_guest_fixture as runtime
from tests.test_modbus_service import service_fixture


def test_a_patient_reader_gets_the_last_published_state_during_a_long_command(tmp_path, monkeypatch):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            monkeypatch.setattr(service_runtime, "_PATIENCE_SECONDS", 0.01)
            first = await service.stable_snapshot(patient=True)
            assert service.published[1] is first
            async with service.command_lock:
                # The poll does not wait for the command.
                assert await service.stable_snapshot(patient=True) is first
                # A strict reader, such as a transaction's answer, still does.
                strict = asyncio.create_task(service.stable_snapshot())
                await asyncio.sleep(0.03)
                assert not strict.done()
                # An old state is not served: a hanging command stays visible.
                service.published = (service.published[0] - 61, first)
                with pytest.raises(TimeoutError):
                    await service.stable_snapshot(patient=True)
            fresh = await strict
            assert fresh is not first
        finally:
            await service.stop()
    asyncio.run(run())


def test_a_command_that_ends_in_time_is_waited_for(tmp_path, monkeypatch):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            first = await service.stable_snapshot(patient=True)
            await service.command_lock.acquire()
            poll = asyncio.create_task(service.stable_snapshot(patient=True))
            await asyncio.sleep(0.01)
            service.command_lock.release()
            assert await poll is not first
            assert not service.command_lock.locked()
            # Without anything published yet, even a patient reader waits.
            service.published = None
            async with service.command_lock:
                monkeypatch.setattr(service_runtime, "_PATIENCE_SECONDS", 0.01)
                with pytest.raises(TimeoutError):
                    await service.stable_snapshot(patient=True)
        finally:
            await service.stop()
    asyncio.run(run())


def test_every_completed_command_publishes_its_state(tmp_path, monkeypatch):

    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            serial = service.identity.serial_number
            before = await service.stable_snapshot(patient=True)
            result = await service.command({"command": "pause", "expected_serial_number": serial})
            assert service.published[1] is result
            assert result is not before
            # A plan change beside a running command drops the older state
            # instead of letting a poll report the plan as lost.
            request = {
                "command": "departure_inputs", "value": DepartureInputs("profile:a:b").snapshot(),
                "expected_serial_number": serial,
            }
            async with service.command_lock:
                await service.command(request)
                assert service.published is None
            result = await service.command(request)
            assert service.published[1] is result
    asyncio.run(run())
