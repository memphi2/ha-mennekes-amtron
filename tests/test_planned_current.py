"""A planner's current is its own: it never becomes the one a resume returns to."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from tests import service_guest_fixture as runtime
from tests.fakes import FakeModbusClient, device_bank
from tests.test_departure_planner import hand_over


@asynccontextmanager
async def svc(tmp_path, cap):
    transport = FakeModbusClient(device_bank(evse_state=2, charging_current=8))
    clock = [1000.0]
    client = MennekesModbusClient(
        SerialConfig("/dev/fake"),
        client_factory=lambda _: transport,
        monotonic=lambda: clock[0],
    )
    service = ModbusService(
        ServerConfig(client.config, runtime.TOKEN, read_only=False, current_cap=cap),
        tmp_path / "intent.json",
        client=client,
    )
    try:
        await service._initialize()
        await service.heartbeat.async_stop()
        transport.writes.clear()
        service.control._monotonic = lambda: clock[0]
        yield service, transport, clock
    finally:
        await service.stop()


async def withdraw(service):
    await service.command(
        {
            "command": "departure_trip",
            "deadline": None,
            "source": None,
            "withdraw": True,
            "expected_serial_number": service.identity.serial_number,
        }
    )


@pytest.mark.parametrize("cap", [16, 13.2])
def test_a_pause_after_the_plan_returns_to_the_chosen_current(tmp_path, cap):
    async def run():
        async with svc(tmp_path, cap) as (service, transport, clock):
            serial = service.identity.serial_number
            chosen = service.control.charging_setpoint
            await hand_over(service, hours=2)
            await service.departure.tick()
            assert runtime.current_writes(transport)[-1] == pytest.approx(cap)
            assert service.control.charging_setpoint == chosen
            await withdraw(service)
            clock[0] += 1000
            await service.departure.tick()
            await service.refresh()
            await service.command(
                {"command": "pause", "expected_serial_number": serial}
            )
            clock[0] += 1000
            await service.command(
                {"command": "resume", "expected_serial_number": serial}
            )
            assert runtime.current_writes(transport)[-1] == chosen

    asyncio.run(run())


def test_lowering_a_plans_current_to_the_cap_keeps_the_chosen_one(tmp_path):
    async def run():
        async with svc(tmp_path, 16) as (service, transport, clock):
            control = service.control
            chosen = control.charging_setpoint
            await hand_over(service, hours=2)
            await service.departure.tick()
            assert runtime.current_writes(transport)[-1] == 16
            clock[0] += 1000
            await service.refresh()
            await control.async_set_limits(replace(control.options, current_cap=12.0))
            assert await control.async_enforce_current_cap()
            assert runtime.current_writes(transport)[-1] == 12
            assert control.charging_setpoint == chosen

    asyncio.run(run())


def test_an_error_recovery_during_a_plan_keeps_both_currents(tmp_path):
    async def run():
        async with svc(tmp_path, 16) as (service, transport, clock):
            control = service.control
            chosen = control.charging_setpoint
            await hand_over(service, hours=2)
            await service.departure.tick()
            assert runtime.current_writes(transport)[-1] == 16
            clock[0] += 1000
            runtime.put(transport.bank, R.ERROR_CODE, 200)
            await service.refresh()
            assert await control.async_recover_from_error()
            # The plan's current is reasserted, the chosen one is kept.
            assert runtime.current_writes(transport)[-1] == 16
            assert control.charging_setpoint == chosen

    asyncio.run(run())


def test_a_current_set_by_hand_during_a_plan_is_the_chosen_one(tmp_path):
    async def run():
        async with svc(tmp_path, 16) as (service, _transport, clock):
            serial = service.identity.serial_number
            await hand_over(service, hours=2)
            await service.departure.tick()
            assert service.control.writes.line_planned
            clock[0] += 1000
            await service.command({"command": "set_current", "value": 10, "expected_serial_number": serial})
            assert not service.control.writes.line_planned
            assert service.control.charging_setpoint == 10

    asyncio.run(run())


def test_a_cap_that_has_to_wait_keeps_the_chosen_current_too(tmp_path):
    async def run():
        async with svc(tmp_path, 16) as (service, transport, clock):
            control = service.control
            chosen = control.charging_setpoint
            await hand_over(service, hours=2)
            await service.departure.tick()
            assert runtime.current_writes(transport)[-1] == 16
            await service.refresh()
            await control.async_set_limits(replace(control.options, current_cap=12.0))
            # Inside the interval between two current writes the cap waits.
            await control.async_enforce_current_cap()
            assert control._pending is not None
            assert control.charging_setpoint == chosen
            clock[0] += 10
            control._pending_due = 0
            control._pending_changed.set()
            for _ in range(50):
                await asyncio.sleep(0.01)
                if control._pending is None:
                    break
            assert runtime.current_writes(transport)[-1] == 12
            assert control.charging_setpoint == chosen

    asyncio.run(run())
