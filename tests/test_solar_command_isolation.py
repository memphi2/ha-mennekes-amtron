"""Independent solar controls cannot inherit an in-flight trip's cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import control as control_module
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import SolarChargingMode
from tests.test_timed_control import Harness


@pytest.mark.parametrize("reason", ["goal_reached", "deadline_expired"])
def test_solar_waits_for_timed_current_and_keeps_its_own_command_context(monkeypatch, reason):
    async def run():
        harness = Harness()
        control = await harness.connect()
        identity_started, release_identity = asyncio.Event(), asyncio.Event()
        transaction_client = harness.client._async_transaction_client
        deadline = datetime.now(UTC) + timedelta(hours=1)

        async def delayed_identity(**kwargs):
            transport = await transaction_client(**kwargs)
            identity_started.set()
            await release_identity.wait()
            return transport

        monkeypatch.setattr(harness.client, "_async_transaction_client", delayed_identity)
        timed = asyncio.create_task(control.async_set_timed_current(
            6, deadline=deadline, owner="timed:one-trip",
        ))
        solar = None
        try:
            await asyncio.wait_for(identity_started.wait(), 1)
            if reason == "goal_reached":
                control.soc_goal.observe(80)
            else:
                class ExpiredDatetime(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return deadline + timedelta(seconds=1)

                monkeypatch.setattr(control_module, "datetime", ExpiredDatetime)
            solar = asyncio.create_task(control.async_set_solar_charging_mode(SolarChargingMode(1)))
            await asyncio.sleep(0)
            assert not solar.done()
            release_identity.set()
            await asyncio.wait_for(asyncio.gather(timed, solar), 1)

            assert harness.written(R.CHARGING_CURRENT_EMS) == []
            assert harness.written(R.SOLAR_CHARGING_MODE) == [[1]]
            assert control.pending_action is None
            assert control._active_command is None
            events = control.events.snapshot()["events"]
            assert len(events) == 1
            assert events[0]["outcome"] == "cancelled"
            assert events[0]["action"] == "set_current"
            assert events[0]["reason"] == ("energy_goal" if reason == "goal_reached" else reason)
        finally:
            release_identity.set()
            await asyncio.gather(timed, *([solar] if solar is not None else []), return_exceptions=True)
            await control.async_shutdown()
            await harness.client.async_close()

    asyncio.run(run())
