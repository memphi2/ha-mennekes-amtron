"""A reached ceiling fences starts after bus and identity waits, before sending."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.soc_goal_controller import (
    SocGoalController,
)
from tests.fakes import put
from tests.test_energy_goal_controller import _runner, _settle, _snapshot

SOURCE = "sensor.car_soc|"


@pytest.mark.parametrize("goal", ["energy", "soc"])
@pytest.mark.parametrize(("wait", "action"), [
    ("bus", "resume"), ("bus", "current"), ("bus", "unlimited"),
    ("identity", "resume"), ("identity", "current"),
    ("identity", "unlimited"), ("identity", "release"), ("identity", "unlock"),
])
def test_reached_goal_during_bus_or_identity_wait_never_sends_start(goal, wait, action):
    async def run():
        harness, control, energy, _ = await _runner(paused=True)
        soc = SocGoalController(control, AsyncMock(), sleep=harness._sleep)
        if goal == "energy":
            await energy.async_set_target(1)
        else:
            await control.async_configure_soc(SOURCE)
            await control.async_set_soc_goal(80, source=SOURCE, soc=20)
        put(harness.transport.bank, R.CHARGING_RELEASE, 0)
        put(harness.transport.bank, R.LOCK_EVSE, 1)
        entered, release = asyncio.Event(), asyncio.Event()
        transaction = harness.client._async_transaction_client

        async def held_identity(*, verify_identity=False):
            result = await transaction(verify_identity=verify_identity)
            if verify_identity:
                entered.set()
                await release.wait()
            return result

        if wait == "bus":
            await harness.client._lock.acquire()
        else:
            harness.client._async_transaction_client = held_identity
        command = {
            "resume": control.async_resume_charging,
            "current": lambda: control.async_set_charging_current(6),
            "unlimited": lambda: control.async_set_charging_current(0, allow_unlimited=True),
            "release": lambda: control.async_set_charging_release(True),
            "unlock": lambda: control.async_set_lock_evse(False),
        }[action]
        start = asyncio.create_task(command())
        observation = None
        try:
            if wait == "identity":
                await asyncio.wait_for(entered.wait(), 1)
            else:
                await asyncio.sleep(0)
                assert control.energy_goal_guard.locked()
            if goal == "energy":
                energy.observe(_snapshot(harness, 1235.5))
            else:
                observation = asyncio.create_task(control.async_observe_soc(80, source=SOURCE))
                await asyncio.sleep(0)
            assert control.energy_goal_stop_requested
            if wait == "bus":
                harness.client._lock.release()
            release.set()
            with pytest.raises(ServiceValidationError) as error:
                await start
            assert error.value.translation_key == "energy_goal_stop_active"
            if observation is not None:
                await observation
            await _settle(energy if goal == "energy" else soc)
            assert harness.transport.writes == []
            assert harness.slept == []
            assert control.is_paused
            assert control.charging_setpoint == 10
            # A refused start consumed no register cooldown: after the user
            # explicitly disables the reached ceiling, the same action works.
            if goal == "energy":
                await energy.async_set_target(0)
            else:
                await control.async_set_soc_goal(0, source=SOURCE)
            await command()
            assert len(harness.transport.writes) == 1
            assert harness.slept == []
        finally:
            release.set()
            if wait == "bus" and harness.client._lock.locked():
                harness.client._lock.release()
            await asyncio.gather(start, *([observation] if observation else []), return_exceptions=True)
            await soc.async_stop()
            await energy.async_stop()
            await control.async_shutdown()

    asyncio.run(asyncio.wait_for(run(), 5))
