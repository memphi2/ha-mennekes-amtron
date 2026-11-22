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
from custom_components.mennekes_amtron_rtu.device.energy_goal_controller import (
    EnergyGoalController,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceValidationError
from tests.fakes import put
from tests.test_energy_goal_controller import _runner, _settle, _snapshot


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
    assert energy_for(10, 5e-324) == 1000.0


@pytest.mark.parametrize("raw", [
    None, {}, {"amount": 1, "spent": 0, "price": 0.3},
    {"amount": True, "spent": 0, "price": 0.3, "booked_kwh": 0},
    {"amount": 0, "spent": 0, "price": 0.3, "booked_kwh": 0},
    {"amount": 1, "spent": float("nan"), "price": 0.3, "booked_kwh": 0},
    {"amount": 1, "spent": 0, "price": 0.3, "booked_kwh": -1},
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
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        assert control.energy_goal.target_kwh == 33.3
        assert control.energy_goal.cost == CostPart(10.0, 0.0, 0.30, currency="EUR")
        assert saved[-1]["cost"] == {"amount": 10.0, "spent": 0.0, "price": 0.30, "booked_kwh": 0.0, "currency": "EUR"}
        generation = harness.client.write_state.energy_goal_generation
        runner.observe(_snapshot(harness, 110.0))
        await _settle(runner)
        clock[0] += 61
        await runner.async_set_price(0.50, currency="EUR")
        # 10 kWh at 0.30 cost 3; 7 left at 0.50 is 14 kWh more: 24 from 100.
        goal = control.energy_goal
        assert (goal.target_kwh, goal.baseline_kwh, goal.status) == (24.0, 100.0, "active")
        assert harness.client.write_state.energy_goal_generation == generation
        cost = goal.cost
        assert (cost.amount, cost.spent, cost.price, cost.booked_kwh) == (10.0, pytest.approx(3.0), 0.50, 10.0)
        assert saved[-1]["cost"]["price"] == 0.50
        runner.observe(_snapshot(harness, 114.0))
        await _settle(runner)
        clock[0] += 61
        await runner.async_set_price(0.25, currency="EUR")
        # 4 more kWh at 0.50 cost 2; 5 left at 0.25 is 20 kWh more than 14.
        assert control.energy_goal.target_kwh == 34.0
        assert control.energy_goal.cost.spent == pytest.approx(5.0)
        await runner.async_stop()
    asyncio.run(run())


def test_the_price_waits_a_minute_and_ignores_small_changes():
    async def run():
        _harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        await runner.async_set_price(0.50, currency="EUR")
        clock[0] += 61
        await runner.async_set_price(0.3005)
        assert control.energy_goal.target_kwh == 33.3
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.target_kwh == 20.0
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_follows_only_a_running_amount():
    async def run():
        _harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.target_kwh == 0
        await runner.async_set_target(5)
        clock[0] += 61
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.target_kwh == 5
        assert control.energy_goal.cost is None
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        control.energy_goal.observe(None)
        clock[0] += 61
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.target_kwh == 33.3
        await runner.async_stop()
    asyncio.run(run())


def test_less_than_a_step_left_leaves_one_step_at_the_new_price():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(1, 0.30, currency="EUR")
        runner.observe(_snapshot(harness, 103.2))
        await _settle(runner)
        clock[0] += 61
        await runner.async_set_price(0.50, currency="EUR")
        # 0.04 left buys no step at 0.50: one step from the 3.2 kWh so far.
        assert control.energy_goal.target_kwh == 3.3
        assert control.energy_goal.cost.price == 0.50
        assert control.energy_goal.cost.spent == pytest.approx(0.96)
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_keeps_a_stop_that_a_poll_latched_meanwhile():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        clock[0] += 61
        async with control.energy_goal_guard:
            # A command holds the guard while a poll sees the meter fall.
            runner.observe(_snapshot(harness, 50.0))
            price = asyncio.create_task(runner.async_set_price(0.50))
            await asyncio.sleep(0)
        await price
        await _settle(runner)
        goal = control.energy_goal
        assert (goal.status, goal.target_kwh, goal.cost.price) == ("meter_reset", 33.3, 0.30)
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_folds_into_the_copy_a_poll_left_meanwhile():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        clock[0] += 61
        async with control.energy_goal_guard:
            # The price waits for the guard first, then a poll leaves its copy.
            price = asyncio.create_task(runner.async_set_price(0.50))
            await asyncio.sleep(0)
            runner.observe(_snapshot(harness, 110.0))
        await price
        await _settle(runner)
        assert control.energy_goal.target_kwh == 24.0
        assert control.energy_goal.cost.booked_kwh == 10.0
        runner._stopping = True
        with pytest.raises(DeviceValidationError, match="energy_goal_unavailable"):
            await runner.async_set_price(0.25, currency="EUR")
        runner._stopping = False
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_keeps_a_queued_recovery_resume():
    async def run():
        _harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        clock[0] += 61
        calls = []
        control.cancel_goal_resume = lambda *a, **k: calls.append(1)
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.cost.price == 0.50
        assert calls == []
        await runner.async_stop()
    asyncio.run(run())


def test_a_price_after_a_restart_books_nothing_twice():
    goal = EnergyGoal()
    goal.set_target(24.0, 100.0)
    goal.cost = CostPart(10.0, 3.0, 0.50, 10.0)
    restored = EnergyGoal.restore(goal.snapshot())
    assert restored.reprice(0.25)
    # Before the first poll only the booked 10 kWh are known.
    assert restored.cost.spent == 3.0
    assert restored.target_kwh == 38.0


def test_zero_ends_the_amount_with_its_target_but_keeps_a_plain_one():
    async def run():
        _harness, control, runner, _saved, _clock = await _cost_runner()
        await runner.async_set_target(5)
        await runner.async_set_cost_target(0, None)
        assert control.energy_goal.target_kwh == 5
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        await runner.async_set_cost_target(0, None)
        assert control.energy_goal.target_kwh == 0
        assert control.energy_goal.cost is None
        await runner.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize(("amount", "price", "key"), [
    (True, 0.3, "cost_goal_invalid"), (float("nan"), 0.3, "cost_goal_invalid"),
    (0.05, 0.3, "cost_goal_invalid"), (1001, 0.3, "cost_goal_invalid"),
    (10, None, "cost_goal_price_unknown"), (10, float("inf"), "cost_goal_price_unknown"),
    (10**400, 0.3, "cost_goal_invalid"), (10, 10**400, "cost_goal_price_unknown"),
    (10, -1e308, "cost_goal_price_unknown"), (10, 100.01, "cost_goal_price_unknown"),
])
def test_an_invalid_amount_or_price_is_refused(amount, price, key):
    async def run():
        _harness, control, runner, _saved, _clock = await _cost_runner()
        with pytest.raises(DeviceValidationError, match=key):
            await runner.async_set_cost_target(amount, price, currency="EUR")
        with pytest.raises(DeviceValidationError, match="cost_goal_price_unknown"):
            await runner.async_set_price("0.3")
        assert control.energy_goal.target_kwh == 0
        await runner.async_stop()
    asyncio.run(run())


def test_only_a_running_amount_takes_a_price():
    assert not EnergyGoal().reprice(0.5)
    goal = EnergyGoal()
    goal.set_target(5, 100.0)
    assert not goal.reprice(0.5)


def test_the_last_reported_price_arms_an_amount_without_one():
    async def run():
        _harness, control, runner, _saved, _clock = await _cost_runner()
        with pytest.raises(DeviceValidationError, match="cost_goal_price_unknown"):
            await runner.async_set_cost_target(10)
        await runner.async_set_price(0.25, currency="EUR")
        assert runner.price == 0.25
        await runner.async_set_cost_target(10)
        assert control.energy_goal.target_kwh == 40.0
        assert control.energy_goal.cost == CostPart(10.0, 0.0, 0.25, currency="EUR")
        await runner.async_stop()
    asyncio.run(run())


def test_the_money_used_follows_the_polled_meter_between_prices():
    goal = EnergyGoal()
    assert goal.cost_spent is None
    goal.set_target(33.3, 100.0)
    goal.cost = CostPart(10.0, 0.0, 0.30)
    goal.observe(110.0)
    assert goal.cost_spent == pytest.approx(3.0)
    assert goal.reprice(0.50)
    goal.observe(112.0)
    assert goal.cost_spent == pytest.approx(4.0)


def test_the_price_of_an_amount_is_the_newest_price_known():
    async def run():
        harness, control, runner, _saved, _clock = await _cost_runner()
        await runner.async_set_price(0.30, currency="EUR")
        await runner.async_set_cost_target(10, 0.40)
        # The app arms at 0.40, not at the price reported before the amount.
        assert runner.price == 0.40
        await runner.async_set_cost_target(15)
        assert control.energy_goal.target_kwh == 37.5
        await runner.async_stop()
        # After a restart the restored amount still names its price.
        again = EnergyGoalController(control, harness.client, runner._persist)
        assert again.price == 0.40
        await again.async_set_cost_target(20)
        assert control.energy_goal.target_kwh == 50.0
        await again.async_stop()
    asyncio.run(run())


def test_a_price_that_changes_nothing_opens_no_waiting_minute():
    async def run():
        harness, control, runner, _saved, clock = await _cost_runner()
        await runner.async_set_cost_target(10, 0.30, currency="EUR")
        clock[0] += 61
        runner.observe(_snapshot(harness, None))
        await _settle(runner)
        assert control.energy_goal.status == "meter_unavailable"
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.cost.price == 0.30
        runner.observe(_snapshot(harness, 100.0))
        await _settle(runner)
        clock[0] += 1
        await runner.async_set_price(0.50, currency="EUR")
        assert control.energy_goal.cost.price == 0.50
        await runner.async_stop()
    asyncio.run(run())


def test_the_money_used_at_the_end_counts_the_last_reading_and_survives_a_restart():
    goal = EnergyGoal()
    goal.set_target(33.3, 100.0)
    goal.cost = CostPart(10.0, 0.0, 0.30)
    goal.observe(130.0)
    assert goal.cost_spent == pytest.approx(9.0)
    # The reading that reaches the target is the one that counts.
    assert goal.observe(133.4)
    assert goal.status == "reached"
    assert goal.cost_spent == pytest.approx(10.02)
    # The money used is part of what is saved, not only of the running owner.
    restored = EnergyGoal.restore(goal.snapshot())
    assert restored.cost_spent == pytest.approx(10.02)
    # A later reading changes nothing once the target is reached.
    assert not goal.observe(140.0)
    assert goal.cost_spent == pytest.approx(10.02)


def test_a_reached_amount_is_saved_with_its_money_and_shown_after_a_restart():
    async def run():
        harness, control, runner, saved, _clock = await _cost_runner()
        await runner.async_set_cost_target(3, 0.30, currency="EUR")
        assert control.energy_goal.target_kwh == 10.0
        runner.observe(_snapshot(harness, 109.0))
        await _settle(runner)
        assert control.energy_goal.cost_spent == pytest.approx(2.7)
        # The reading that reaches the target is saved with the money it used.
        runner.observe(_snapshot(harness, 110.0))
        await _settle(runner)
        assert control.energy_goal.status == "reached"
        assert control.energy_goal.cost_spent == pytest.approx(3.0)
        assert saved[-1]["status"] == "reached"
        # What a restarted service finds on disk shows the same money.
        assert EnergyGoal.restore(saved[-1]).cost_spent == pytest.approx(3.0)
        await runner.async_stop()
    asyncio.run(run())


def test_progress_follows_the_meter_for_energy_and_money():
    goal = EnergyGoal()
    assert goal.progress(100.0) is None
    goal.set_target(10, 100.0)
    assert goal.progress(100.0) == 0
    assert goal.progress(102.5) == pytest.approx(0.25)
    # Until the goal latches it stays below a full target.
    assert goal.progress(109.96) == 0.99
    assert goal.progress(120.0) == 0.99
    # No usable reading, or a meter below the baseline: no progress.
    for reading in (None, "x", float("nan"), True, 99.0):
        assert goal.progress(reading) is None
    goal.cost = CostPart(5.0, 1.0, 0.5, 2.0)
    # 1.00 for the first 2 kWh, then 0.50 per kWh: 4 kWh cost 2.00 of 5.00.
    assert goal.progress(104.0) == pytest.approx(0.4)
    # Below the booked energy the booked money still counts.
    assert goal.progress(101.0) == pytest.approx(0.2)
    goal.cost = CostPart(5.0, 1.0, -0.5, 2.0)
    assert goal.progress(110.0) == 0
    goal.observe(None)
    assert goal.status == "meter_unavailable"
    assert goal.progress(104.0) is None
    goal.cost = None
    goal.observe(110.0)
    assert goal.status == "reached"
    assert goal.progress(None) == 1.0


def test_a_cached_price_cannot_arm_a_target_in_another_currency():
    async def run():
        _, _, runner, _, _ = await _cost_runner()
        await runner.async_set_price(0.30, currency="EUR")
        with pytest.raises(DeviceValidationError, match="cost_goal_price_unknown"):
            await runner.async_set_cost_target(10, currency="CHF")
        assert runner.model.cost is None
        assert (runner.price, runner.currency) == (0.30, "EUR")
        await runner.async_stop()

    asyncio.run(run())


def test_failed_repricing_keeps_the_same_ceiling_on_disk_and_after_restart(tmp_path, monkeypatch):
    from tests.test_modbus_service import service_fixture

    async def run():
        service, _, clock = await service_fixture(tmp_path)
        try:
            goal = service.goal
            goal._monotonic = lambda: clock[0]
            await goal.async_set_cost_target(10, 0.25, currency="EUR")
            clock[0] += 61

            def fail(_state):
                raise OSError("disk full before replacement")

            with monkeypatch.context() as patch:
                patch.setattr(service._store, "save", fail)
                with pytest.raises(OSError, match="disk full"):
                    await goal.async_set_price(0.50, currency="EUR")
            assert (goal.model.target_kwh, goal.price) == (40, 0.25)
            restarted, _, _ = await service_fixture(tmp_path)
            try:
                assert (restarted.goal.model.target_kwh, restarted.goal.price) == (40, 0.25)
            finally:
                await restarted.stop()
            await goal.async_set_price(0.50, currency="EUR")
            assert goal.model.target_kwh == 20
        finally:
            await service.stop()

    asyncio.run(run())
