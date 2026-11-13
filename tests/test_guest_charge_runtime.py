"""Guest isolation reaches the actual service and existing guarded Modbus writer."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    JobRequestError,
)
from tests.test_guest_charge import request
from tests.test_service_charge_runtime import current_writes, job_service


@pytest.mark.parametrize("target_before", [False, True])
def test_real_service_projects_and_enforces_vehicle_target_conflict(tmp_path, target_before):
    async def run():
        async with job_service(tmp_path) as (service, transport, clock):
            control, jobs = service.control, service.jobs
            control.soc_goal.configure_source("fixture:vehicle")
            if target_before:
                control.soc_goal.set_target(80, soc=20)
            state = jobs.snapshot()
            assert state["vehicle_target_active"] is target_before
            before = (list(transport.reads), list(transport.writes))
            if target_before:
                with pytest.raises(JobRequestError, match="guest_vehicle_target_active"):
                    jobs.preview_guest(request(), revision=state["revision"], scope=state["scope"])
                assert (transport.reads, transport.writes) == before
                control.soc_goal.set_target(0)
            state = jobs.snapshot()
            preview = jobs.preview_guest(request(), revision=state["revision"], scope=state["scope"])
            assert (transport.reads, transport.writes) == before
            await jobs.create(preview["request"], revision=preview["revision"], scope=preview["scope"])
            assert current_writes(transport)[-1] == 12
            clock[0] += 1000
            control.soc_goal.set_target(80, soc=20)
            assert jobs.stop_requested()
            await jobs.tick()
            assert jobs.job.reason == "independent_stop"
            assert current_writes(transport)[-1] == 1
            assert all(address == R.CHARGING_CURRENT_EMS.address for address, _words in transport.writes)
            control.soc_goal.set_target(0)
            await jobs.tick()
            assert jobs.job.terminal
            assert current_writes(transport)[-1] == 1
    asyncio.run(run())
