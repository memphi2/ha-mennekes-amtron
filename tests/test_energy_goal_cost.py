"""The service carries an amount of money as its energy target."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.energy_goal import (
    CostPart,
    EnergyGoal,
    energy_for,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from tests.fakes import put, raise_connection_error
from tests.test_energy_goal_controller import _runner


async def _cost_runner(meter=100.0):
    harness, control, runner, saved = await _runner()
    clock = [1000.0]
    runner._monotonic = lambda: clock[0]
    put(harness.transport.bank, R.ENERGY_TOTAL, meter)
    return harness, control, runner, saved, clock


def test_the_energy_keeps_within_the_amount():
    assert energy_for(10, 0.30) == 33.3
    assert energy_for(0.9, 0.30) == 3.0
    assert energy_for(0.01, 0.30) == 0.1
    assert energy_for(5, 0) == energy_for(5, -0.1) == 1000.0
    assert energy_for(1000, 0.001) == 1000.0


@pytest.mark.parametrize("raw", [
    None, {}, {"amount": 1, "spent": 0}, {"amount": True, "spent": 0, "price": 0.3},
    {"amount": 0, "spent": 0, "price": 0.3}, {"amount": 1, "spent": float("nan"), "price": 0.3},
])
def test_a_malformed_amount_is_refused(raw):
    with pytest.raises(ValueError, match="cost"):
        CostPart.restore(raw)


def test_the_amount_survives_a_restart_and_a_disabled_target_has_none():
    goal = EnergyGoal()
    goal.set_target(33.3, 100.0)
    goal.cost = CostPart(10.0, 0.0, 0.30)
    restored = EnergyGoal.restore(goal.snapshot())
    assert restored.cost == goal.cost
    assert "cost" not in EnergyGoal().snapshot()
    with pytest.raises(ValueError, match="disabled"):
        EnergyGoal.restore({**EnergyGoal().snapshot(), "cost": goal.cost.snapshot()})
    goal.set_target(5, 100.0)
    assert goal.cost is None


def test_an_amount_arms_its_energy_and_a_new_price_books_it():
    async def run():
        harness, control, runner, saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30)
        assert control.energy_goal.target_kwh == 33.3
        assert control.energy_goal.cost == CostPart(10.0, 0.0, 0.30)
        assert saved[-1]["cost"] == {"amount": 10.0, "spent": 0.0, "price": 0.30}
        put(harness.transport.bank, R.ENERGY_TOTAL, 110.0)
        clock[0] += 61
        await runner.async_set_price(0.50)
        # 10 kWh at 0.30 cost 3; 7 left at 0.50 is 14 kWh from 110.
        assert control.energy_goal.target_kwh == 14.0
        assert control.energy_goal.baseline_kwh == 110.0
        cost = control.energy_goal.cost
        assert (cost.amount, cost.spent, cost.price) == (10.0, pytest.approx(3.0), 0.50)
        await runner.async_stop()
    asyncio.run(run())


def test_the_price_waits_a_minute_and_ignores_small_changes():
    async def run():
        _harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30)
        await runner.async_set_price(0.50)
        clock[0] += 61
        await runner.async_set_price(0.3005)
        assert control.energy_goal.target_kwh == 33.3
        await runner.async_set_price(0.50)
        assert control.energy_goal.target_kwh == 20.0
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_follows_only_a_running_amount():
    async def run():
        _harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_price(0.50)
        assert control.energy_goal.target_kwh == 0
        await runner.async_set_target(5)
        clock[0] += 61
        await runner.async_set_price(0.50)
        assert control.energy_goal.target_kwh == 5
        assert control.energy_goal.cost is None
        await runner.async_set_cost_target(10, 0.30)
        control.energy_goal.observe(None)
        clock[0] += 61
        await runner.async_set_price(0.50)
        assert control.energy_goal.target_kwh == 33.3
        await runner.async_stop()
    asyncio.run(run())


def test_less_than_a_step_left_keeps_the_target_in_force():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(1, 0.30)
        put(harness.transport.bank, R.ENERGY_TOTAL, 103.3)
        clock[0] += 61
        await runner.async_set_price(0.50)
        assert control.energy_goal.target_kwh == 3.3
        assert control.energy_goal.baseline_kwh == 100.0
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_found_stale_under_the_guard_changes_nothing():
    async def run():
        _harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30)
        clock[0] += 61
        control.energy_goal.set_target(5, 100.0)
        await runner.async_set_target(None, price=0.50)
        assert control.energy_goal.target_kwh == 5
        await runner.async_stop()
    asyncio.run(run())


def test_a_failed_meter_read_keeps_the_running_amount():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30)
        harness.transport.read_exceptions[R.ENERGY_TOTAL.address] = raise_connection_error()
        clock[0] += 61
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await runner.async_set_price(0.50)
        assert control.energy_goal.cost == CostPart(10.0, 0.0, 0.30)
        await runner.async_stop()
    asyncio.run(run())


def test_zero_ends_the_amount_with_its_target_but_keeps_a_plain_one():
    async def run():
        _harness, control, runner, _saved, _clock = await _cost_runner()
        await runner.async_set_target(5)
        await runner.async_set_cost_target(0, None)
        assert control.energy_goal.target_kwh == 5
        await runner.async_set_cost_target(10, 0.30)
        await runner.async_set_cost_target(0, None)
        assert control.energy_goal.target_kwh == 0
        assert control.energy_goal.cost is None
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize(("amount", "price", "key"), [
    (True, 0.3, "cost_goal_invalid"), (float("nan"), 0.3, "cost_goal_invalid"),
    (0.05, 0.3, "cost_goal_invalid"), (1001, 0.3, "cost_goal_invalid"),
    (10, None, "cost_goal_price_unknown"), (10, float("inf"), "cost_goal_price_unknown"),
])
def test_an_invalid_amount_or_price_is_refused(amount, price, key):
    async def run():
        _harness, control, runner, _saved, _clock = await _cost_runner()
        with pytest.raises(DeviceValidationError, match=key):
            await runner.async_set_cost_target(amount, price)
        with pytest.raises(DeviceValidationError, match="cost_goal_price_unknown"):
            await runner.async_set_price("0.3")
        assert control.energy_goal.target_kwh == 0
        await runner.async_stop()
    asyncio.run(run())


def test_a_meter_that_is_no_number_keeps_the_running_amount():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30)
        read = harness.client.async_read_register

        async def text(spec, **how):
            return "x" if spec is R.ENERGY_TOTAL else await read(spec, **how)

        harness.client.async_read_register = text
        clock[0] += 61
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await runner.async_set_price(0.50)
        assert control.energy_goal.cost == CostPart(10.0, 0.0, 0.30)
        await runner.async_stop()
    asyncio.run(run())
