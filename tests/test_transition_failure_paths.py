"""A failed phase preparation must never revive an older charging request."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.enums import (
    EvseState,
    RequestedPhases,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.fakes import put
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, service_fixture


@pytest.mark.parametrize("failure", [OSError, RuntimeError])
def test_phase_pause_ack_discards_old_resume_even_if_persistence_fails(failure):
    async def run():
        harness = Harness(values={
            "charging_current_ems": 8,
            "evse_state": EvseState.CHARGING,
        })
        control = await harness.connect()
        try:
            await control.async_set_charging_current(8)
            await control.async_set_charging_current(16)
            assert control.pending_action == "set_current"
            old_worker = control._flush_task
            harness.clock[0] += 6

            async def fail():
                raise failure("state storage unavailable")

            control._request_refresh = fail
            with pytest.raises(failure, match="state storage unavailable"):
                await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)

            assert control.pending_action is None
            assert control._phase_change is None
            assert control.is_paused is True
            control._request_refresh = harness._refresh
            await asyncio.wait_for(old_worker, 1)
            assert control.is_paused is True
            assert [
                decode_register(R.CHARGING_CURRENT_EMS, words)
                for words in harness.written(R.CHARGING_CURRENT_EMS)
            ] == [8, 1]
            assert harness.written(R.REQUESTED_PHASES) == []
        finally:
            await control.async_shutdown()
            await harness.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("target", [RequestedPhases.SINGLE_PHASE, RequestedPhases.ALL_AVAILABLE])
def test_waiting_for_vehicle_has_unknown_deadline_across_service_api(tmp_path, target):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        hold_vehicle = asyncio.Event()

        async def wait(_delay):
            await hold_vehicle.wait()

        service.control._sleep = wait
        put(transport.bank, R.EVSE_STATE, EvseState.CHARGING)
        put(transport.bank, R.CP_STATE, 28)
        put(transport.bank, R.REQUESTED_PHASES, 1 - int(target))
        try:
            await service.control.async_set_requested_phases(target)
            action = "single_phase" if target == RequestedPhases.SINGLE_PHASE else "all_phases"
            assert service.control.pending_action == action
            assert service.control.pending_until is None
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                data = await remote.update()
                assert remote.connected
                assert data.pending_change == action
                assert data.pending_change_until is None
                assert remote.snapshot["control"]["pending_until"] is None
                await remote.async_close()
            assert not any(address == R.REQUESTED_PHASES.address for address, _ in transport.writes)
        finally:
            await service.stop()

    asyncio.run(run())
