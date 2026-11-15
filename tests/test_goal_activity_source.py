"""Command provenance describes its original cause, even after another goal changes."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.fakes import put
from tests.test_control import Harness
from tests.test_energy_goal_controller import _settle, _snapshot
from tests.test_soc_goal_controller import SOURCE, _soc_runner


@pytest.mark.parametrize(("cause", "source", "reason"), [
    ("time", "guest", "time_limit"),
    ("confirmation", "guest", "guest_confirmation"),
    ("energy", "energy_goal", "energy_goal"),
    ("soc", "soc_goal", "energy_goal"),
])
def test_automatic_stop_names_the_actual_limit(cause, source, reason):
    async def run():
        h = Harness(values={"evse_state": EvseState.EV_CONNECTED, "charging_current_ems": 16.0})
        control = await h.connect()
        put(h.transport.bank, R.CP_STATE, CpState.B2)
        if cause == "time":
            control.time_limit_reached = lambda: True
        elif cause == "confirmation":
            control.guest_confirmation_required = lambda: True
        elif cause == "soc":
            control.soc_goal.configure_source(SOURCE)
            control.soc_goal.set_target(70, soc=80)
        else:
            control.energy_goal.set_target(1, 0)
            control.energy_goal.observe(2)
        try:
            await control.async_pause_for_energy_goal()
            event = control.events.snapshot()["events"][-1]
            assert (event["source"], event["reason"]) == (source, reason)
            assert event["action"] == "pause"
            assert event["outcome"] == "executed"
        finally:
            await control.async_shutdown()
    asyncio.run(run())


def test_queued_energy_resume_keeps_its_origin_after_a_new_soc_stop():
    async def run():
        h, control, energy, soc, _ = await _soc_runner()
        waiting = asyncio.Event()
        control._sleep = lambda _: waiting.wait()
        try:
            await control.async_set_soc_goal(80, source=SOURCE, soc=20)
            await energy.async_set_target(12)
            energy.observe(None)
            await _settle(energy)
            energy.observe(_snapshot(h, 1235))
            await _settle(energy)
            assert control.pending_action == "resume"
            assert control._pending.source == "energy_goal"
            await control.async_set_soc_goal(70, source=SOURCE, soc=80)
            cancelled = [event for event in control.events.snapshot()["events"]
                         if event["outcome"] == "cancelled" and event["action"] == "resume"]
            assert len(cancelled) == 1
            assert cancelled[0]["source"] == "energy_goal"
        finally:
            waiting.set()
            await soc.async_stop()
            await energy.async_stop()
            await control.async_shutdown()
    asyncio.run(run())
