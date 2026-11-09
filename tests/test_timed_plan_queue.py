"""Valid changed inputs revoke stale owned commands in both connection modes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.data import WallboxData, WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, current_writes, service_fixture
from tests.test_timed_goal import setup_observer, state


# 48 kWh in five hours start at once at 16 A. Each change makes the
# remainder fit at the minimum current, so the queued 16 A is outdated.
@pytest.mark.parametrize(("change", "expected"), [
    ("soc_near_target", 6), ("capacity", 6), ("soc_progress", 6),
])
@pytest.mark.parametrize("manual", [False, True])
def test_valid_new_inputs_revoke_old_owned_resume(monkeypatch, change, expected, manual):
    async def run():
        observer, _, hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        harness = Harness(state_change_interval_seconds=300, values={
            "evse_state": EvseState.CHARGING, "cp_state": CpState.C2,
            "charging_current_ems": 16,
        })
        monkeypatch.setattr(observer_module, "time", SimpleNamespace(monotonic=lambda: harness.clock[0]))
        control = await harness.connect()
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        control.soc_goal.configure_source("sensor.soc|")
        control.soc_goal.set_target(80, soc=20)
        observer._runtime.control = control
        observer.goal.set(now + timedelta(hours=5))
        try:
            await control.async_pause_charging()
            await observer._evaluate()
            assert control.pending_action == "set_current"
            task = control._flush_task
            if manual:
                await control.async_set_charging_current(12)
            if change.startswith("soc_"):
                hass.states["sensor.soc"] = state(
                    "sensor.soc", "79" if change == "soc_near_target" else "70", unit_of_measurement="%",
                )
            else:
                observer._entry.options["timed_capacity_entity"] = "sensor.capacity"
                hass.states["sensor.capacity"] = state("sensor.capacity", "1", unit_of_measurement="kWh")
            await observer._evaluate()
            assert (control.pending_action is not None) is manual
            assert current_writes(harness.transport) == [1]
            harness.clock[0] += 301
            release.set()
            await asyncio.wait_for(task, 2)
            assert current_writes(harness.transport) == ([1, 12] if manual else [1])
            if not manual:
                await observer._evaluate()
                assert current_writes(harness.transport) == [1, expected]
        finally:
            release.set()
            await observer.async_stop()
            await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("updated_soc", ["20", "20.9"])
def test_unchanged_or_minor_plan_update_retains_its_queued_resume(monkeypatch, updated_soc):
    async def run():
        observer, _, hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        harness = Harness()
        control = await harness.connect()
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        control.soc_goal.configure_source("sensor.soc|")
        control.soc_goal.set_target(80, soc=20)
        observer._runtime.control = control
        observer.goal.set(now + timedelta(hours=5))
        try:
            await control.async_pause_charging()
            await observer._evaluate()
            task = control._flush_task
            hass.states["sensor.soc"] = state("sensor.soc", updated_soc, unit_of_measurement="%")
            await observer._evaluate()
            assert control.pending_action == "set_current"
            assert observer.status == "queued"
            harness.clock[0] += 301
            release.set()
            await asyncio.wait_for(task, 2)
            assert current_writes(harness.transport) == [1, 16]
        finally:
            release.set()
            await observer.async_stop()
            await control.async_shutdown()

    asyncio.run(run())


def test_valid_new_soc_revokes_queued_pause_before_it_stops_charge(monkeypatch):
    async def run():
        observer, _, hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        harness = Harness(state_change_interval_seconds=300, values={
            "evse_state": EvseState.CHARGING, "cp_state": CpState.C2,
            "charging_current_ems": 16,
        })
        monkeypatch.setattr(observer_module, "time", SimpleNamespace(monotonic=lambda: harness.clock[0]))
        control = await harness.connect()
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        control.write_state.mode_change_at = harness.clock[0]
        control.soc_goal.configure_source("sensor.soc|")
        control.soc_goal.set_target(80, soc=79)
        observer._runtime.control = control
        observer.goal.set(now + timedelta(hours=5))
        hass.states["sensor.soc"] = state("sensor.soc", "79", unit_of_measurement="%")
        try:
            await observer._evaluate()
            assert control.pending_action == "pause"
            task = control._flush_task
            hass.states["sensor.soc"] = state("sensor.soc", "20", unit_of_measurement="%")
            await observer._evaluate()
            assert control.pending_action is None
            harness.clock[0] += 301
            release.set()
            await asyncio.wait_for(task, 2)
            assert current_writes(harness.transport) == []
            # The charge never paused and keeps its full current.
            await observer._evaluate()
            assert current_writes(harness.transport) == []
            assert observer.status == "charging"
        finally:
            release.set()
            await observer.async_stop()
            await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("manual", [False, True])
def test_service_cancels_outdated_owned_resume_but_retains_manual_command(tmp_path, monkeypatch, manual):
    async def run():
        observer, _, hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        service, transport, clock = await service_fixture(tmp_path)
        release = asyncio.Event()
        try:
            await service.heartbeat.async_stop()
            service.control._sleep = lambda _delay: release.wait()
            await service.control.async_configure_soc("sensor.soc|")
            await service.control.async_set_soc_goal(80, source="sensor.soc|", soc=20)
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                    state=lambda: WallboxData(), request_refresh=None,
                )
                observer._runtime.control = control
                observer.goal.set(now + timedelta(hours=5))
                await control.async_pause_charging()
                await observer._evaluate()
                assert service.control.pending_action == "set_current"
                task = service.control._flush_task
                if manual:
                    await control.async_set_charging_current(12)
                hass.states["sensor.soc"] = state("sensor.soc", "79", unit_of_measurement="%")
                await observer._evaluate()
                assert (service.control.pending_action is not None) is manual
                clock[0] += 301
                release.set()
                await asyncio.wait_for(task, 2)
                assert current_writes(transport) == ([1, 12] if manual else [1])
                await observer.async_stop()
                await remote.async_close()
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())
