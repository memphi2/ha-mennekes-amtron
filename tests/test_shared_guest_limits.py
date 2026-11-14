"""A guest button uses shared limits without importing a vehicle's target."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
    DepartureModbusService,
)
from tests import test_service_charge_runtime as runtime
from tests.fakes import put


@pytest.mark.parametrize("pnc", [False, True])
@pytest.mark.parametrize("bounded", [False, True])
def test_guest_starts_from_pause_and_keeps_shared_goals(tmp_path, monkeypatch, pnc, bounded):
    async def body():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, clock):
            control = service.control
            if pnc:
                await control.async_configure_plug_charge(True, policy="a" * 64)
            await control.async_configure_soc("car")
            await control.async_set_soc_goal(80, source="car", soc=90)
            await asyncio.sleep(0)
            clock[0] += 1000
            await control.async_pause_charging()
            deadline = datetime.now(UTC) + timedelta(hours=1) if bounded else None
            await service.departure.set_trip(deadline, "manual")
            if bounded:
                await control.async_set_energy_goal(5)
            energy = control.energy_goal.snapshot()
            clock[0] += 1000
            await service.refresh()
            await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                   "expected_guest_revision":service.guest_revision})
            await service.refresh()
            await service.jobs.tick()
            assert service.jobs.job.finish_at == deadline
            assert service.jobs.job.state == "charging"
            assert control.soc_goal.target_percent == 80
            assert control.energy_goal.snapshot() == energy
            assert control.pause_requested is False
            assert runtime.current_writes(transport)[-1] >= 6
            assert control.plug_charge["enabled"] is pnc
            if bounded:
                read = service.jobs.inputs
                service.jobs.inputs = lambda: replace(read(), now=deadline + timedelta(seconds=1))
                clock[0] += 1000
                await service.jobs.tick()
                assert service.jobs.job.state == "finished"
                assert runtime.current_writes(transport)[-1] < 6
            put(transport.bank, R.EVSE_STATE, 1)
            put(transport.bank, R.CP_STATE, 10)
            await service.refresh()
            assert not control.guest_active()
    asyncio.run(body())


@pytest.mark.parametrize("denial", ["stale", "identity", "readonly", "lock", "fault", "expired"])
def test_guest_refusal_never_grants_release(tmp_path, monkeypatch, denial):
    from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
    async def body():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, _clock):
            payload = {"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                       "expected_guest_revision": service.guest_revision}
            if denial == "stale":
                payload["expected_guest_revision"] = "stale"
            elif denial == "identity":
                payload["expected_serial_number"] = "other"
            elif denial == "readonly":
                service.control._options = replace(service.control._options, read_only=True)
            elif denial in ("lock", "fault"):
                put(transport.bank, R.LOCK_EVSE if denial == "lock" else R.ERROR_CODE, 1)
                await service.refresh()
            else:
                await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
                payload["expected_guest_revision"] = service.guest_revision
            before = list(transport.writes)
            with pytest.raises(DeviceError):
                await service.command(payload)
            assert transport.writes == before
            assert service.jobs.job is None
    asyncio.run(body())


def test_guest_uses_existing_energy_baseline_and_stops_before_time(tmp_path, monkeypatch):
    async def body():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, clock):
            control = service.control
            await control.async_set_energy_goal(1)
            baseline = control.energy_goal.baseline_kwh
            await service.departure.set_trip(datetime.now(UTC) + timedelta(hours=1), "manual")
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest_revision})
            assert control.energy_goal.baseline_kwh == baseline
            clock[0] += 1000
            put(transport.bank, R.ENERGY_TOTAL, baseline + 1.1)
            service._poller._slow_read_at = None
            await service.refresh()
            await service.jobs.tick()
            assert service.jobs.job.terminal
            assert runtime.current_writes(transport)[-1] < 6
    asyncio.run(body())


def test_guest_limit_change_while_saving_refuses_old_start(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
    async def body():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with runtime.job_service(tmp_path) as (service, transport, _clock):
            save = service.jobs._save
            changed = False
            async def racing_save():
                nonlocal changed
                await save()
                if not changed:
                    changed = True
                    await service.control.async_set_energy_goal(3)
            monkeypatch.setattr(service.jobs, "_save", racing_save)
            before = list(transport.writes)
            with pytest.raises(DeviceError, match="soc_goal_source_changed"):
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": service.guest_revision})
            assert service.jobs.job.terminal
            assert transport.writes == before
    asyncio.run(body())


def test_shared_guest_storage_failure_never_grants_release(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
        JobRequestError,
    )
    from tests.test_guest_charge import NOW, Harness
    async def body():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        grant = AsyncMock()
        def fail(_job):
            raise OSError("fixture storage unavailable")
        monkeypatch.setattr(c.store, "save", fail)
        with pytest.raises(JobRequestError, match="job_storage_unavailable"):
            await c.start_shared({"id":"a"*32, "kind":"guest", "start_at":NOW.isoformat(), "finish_at":None,
                                  "energy_kwh":0, "current_a":8}, grant)
        grant.assert_not_awaited()
    asyncio.run(body())


def test_guest_deadline_edit_and_clear_remain_restorable(tmp_path):
    from unittest.mock import AsyncMock

    from custom_components.mennekes_amtron_rtu.device.charge_job import ChargeJob
    from tests.test_guest_charge import NOW, Harness
    async def body():
        h = Harness(tmp_path)
        c = h.controller
        await c.initialize()
        await c.update_guest_deadline(None)
        await c.start_shared({"id":"a"*32, "kind":"guest", "start_at":NOW.isoformat(), "finish_at":None,
                              "energy_kwh":0, "current_a":8}, AsyncMock())
        await c.update_guest_deadline(NOW + timedelta(hours=1))
        assert c.job.finish_at == NOW + timedelta(hours=1)
        await c.update_guest_deadline(None)
        assert ChargeJob.restore(c.job.snapshot(), restarted=False).finish_at is None
        await c.update_guest_deadline(NOW - timedelta(seconds=1))
        assert c.job.terminal
        assert ChargeJob.restore(c.job.snapshot(), restarted=False).terminal
    asyncio.run(body())
