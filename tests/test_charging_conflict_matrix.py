"""Cross-feature stop precedence on the direct and authenticated HTTP paths."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from itertools import combinations
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.test_modbus_service import TOKEN, current_writes
from tests.test_plug_charge_observer import observer_harness, plug_in_paused
from tests.test_plug_charge_remote import remote_control
from tests.test_timed_control import OWNER, SOC_SOURCE, Harness, service_fixture

POLICY = "c" * 64
BLOCKERS = ("manual_stop", "lock", "rfid", "error", "energy_goal")


@asynccontextmanager
async def control_path(tmp_path, *, remote):
    """One active SoC target and a paused, connected, not-yet-granted car."""
    async with AsyncExitStack() as stack:
        if remote:
            service, transport, clock = await service_fixture(tmp_path)
            stack.push_async_callback(service.stop)
            # Timing here belongs to the command queue, not heartbeat tests.
            await service.heartbeat.async_stop()
            server = await stack.enter_async_context(TestServer(create_app(service)))
            session = await stack.enter_async_context(ClientSession())
            client = RemoteClient(session, str(server.make_url("/")), TOKEN)
            await client.async_connect()
            stack.push_async_callback(client.async_close)
            control, owner = remote_control(client), service.control
        else:
            harness = Harness()
            control = owner = await harness.connect()
            stack.push_async_callback(harness.client.async_close)
            stack.push_async_callback(owner.async_shutdown)
            transport, clock = harness.transport, harness.clock
        await control.async_configure_plug_charge(True, policy=POLICY)
        transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        transport.bank[R.CP_STATE.address] = int(CpState.B1)
        if remote:
            await service.refresh()
            await client.update()
        else:
            harness.snapshot = WallboxData(values={
                "evse_state": int(EvseState.EV_CONNECTED), "cp_state": int(CpState.B1),
                "charging_release": 0, "charging_current_ems": 16.0,
            })
            owner.observe_plug_charge(harness.snapshot)
        clock[0] += 301
        await control.async_pause_charging()
        clock[0] += 301
        assert owner.pause_requested is True
        assert owner.plug_charge["session"] is not None
        assert not owner.plug_charge["authorized"]
        yield SimpleNamespace(control=control, owner=owner, transport=transport, clock=clock)


def blocker(path, name, enabled):
    """Inject independent live inputs; do not confuse a goal with a bus write."""
    if name == "manual_stop":
        path.owner.charging_release_requested = False if enabled else None
    elif name == "energy_goal":
        goal = path.owner.energy_goal
        goal.set_target(1 if enabled else 0, 100 if enabled else None)
        if enabled:
            goal.observe(101)
        assert goal.stop_requested is enabled
    else:
        register, active = {
            "lock": (R.LOCK_EVSE, 1),
            "rfid": (R.AUTHORIZATION_ENABLED, 1),
            "error": (R.ERROR_CODE, 200),
        }[name]
        path.transport.bank[register.address] = active if enabled else 0


async def authorize(path):
    await path.control.async_authorize_plug_charge(
        path.owner.plug_charge["session"],
        deadline=datetime.now(UTC) + timedelta(seconds=10),
    )


@pytest.mark.parametrize("remote", [False, True], ids=["direct", "http-service"])
@pytest.mark.parametrize("reverse", [False, True], ids=["clear-first", "clear-second"])
@pytest.mark.parametrize("pair", list(combinations(BLOCKERS, 2)), ids=lambda pair: "+".join(pair))
def test_clearing_one_stop_never_overrides_another_or_the_pause(tmp_path, remote, reverse, pair):
    async def run():
        async with control_path(tmp_path, remote=remote) as path:
            first, second = reversed(pair) if reverse else pair
            before = list(path.transport.writes)
            for name in pair:
                blocker(path, name, True)
            with pytest.raises(HomeAssistantError):
                await authorize(path)
            assert path.transport.writes == before
            blocker(path, first, False)
            with pytest.raises(HomeAssistantError):
                await authorize(path)
            assert path.transport.writes == before
            assert path.owner.pause_requested is True
            assert not path.owner.plug_charge["authorized"]
            blocker(path, second, False)
            # Removing the last stop is not itself permission to write/resume.
            assert path.transport.writes == before
            await authorize(path)
            assert path.owner.plug_charge["authorized"]
            assert path.owner.pause_requested is True
            assert path.transport.writes[len(before):] == [(R.CHARGING_RELEASE.address, [1])]
    asyncio.run(run())


@pytest.mark.parametrize("remote", [False, True], ids=["direct", "http-service"])
@pytest.mark.parametrize("target", ["energy", "soc"])
def test_reached_target_cancels_queued_departure_even_after_plug_charge_grant(tmp_path, remote, target):
    async def run():
        async with control_path(tmp_path, remote=remote) as path:
            await authorize(path)
            await path.control.async_set_charging_current(12)
            path.clock[0] += 301
            await path.control.async_pause_charging()
            wake = asyncio.Event()
            path.owner._sleep = lambda _delay: wake.wait()
            try:
                await path.control.async_set_timed_current(
                    16, deadline=datetime.now(UTC) + timedelta(hours=1), owner=OWNER,
                )
                assert path.owner.pending_action == "set_current"
                task = path.owner._flush_task
                assert task is not None
                before = current_writes(path.transport)
                if target == "energy":
                    blocker(path, "energy_goal", True)
                else:
                    path.owner.soc_goal.observe(80)
                    assert path.owner.soc_goal.stop_requested
                path.clock[0] += 301
                wake.set()
                await asyncio.wait_for(task, 2)
                assert path.owner.pending_action is None
                assert path.owner.pause_requested is True
                assert current_writes(path.transport) == before
                event = path.owner.events.snapshot()["events"][-1]
                assert (event["outcome"], event["reason"]) == ("cancelled", "energy_goal")
                # The gate grant does not consume independent goal stops.
                assert path.owner.plug_charge["authorized"]
                if target == "energy":
                    blocker(path, "energy_goal", False)
                else:
                    path.owner.soc_goal.set_target(90, soc=80)
                    assert path.owner.soc_goal.source == SOC_SOURCE
                await asyncio.sleep(0)
                assert path.owner.pending_action is None
                assert path.owner.pause_requested is True
                assert current_writes(path.transport) == before
            finally:
                wake.set()
    asyncio.run(run())


@pytest.mark.parametrize("plan", ["queued", "waiting_for_device", "write_pending"])
@pytest.mark.parametrize("stop", ["goal", "manual"])
def test_plan_ready_then_new_stop_invalidates_the_old_settle_timer(tmp_path, monkeypatch, plan, stop):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h, plan=plan)
            assert h.observer.blocked_reason == "departure_plan"
            h.entry.runtime_data.timed_goal.status = "paused_until_start"
            h.coordinator.async_update_listeners()
            assert h.observer.status == "waiting_release"
            old = h.timers[-1]
            if stop == "goal":
                h.control.energy_goal_stop_requested = True
            else:
                h.control.charging_release_requested = False
            h.coordinator.async_update_listeners()
            assert old.cancelled
            old.callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_not_awaited()
            h.control.async_resume_charging.assert_not_awaited()
            h.control.energy_goal_stop_requested = False
            h.control.charging_release_requested = None
            h.coordinator.async_update_listeners()
            fresh = h.timers[-1]
            assert fresh is not old
            old.callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_not_awaited()
            fresh.callback(None)
            await h.hass.async_block_till_done()
            h.control.async_authorize_plug_charge.assert_awaited_once()
            h.control.async_resume_charging.assert_not_awaited()
            assert h.control.pause_requested is True
    asyncio.run(run())
