"""A charge target in money, carried by the service's energy target."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import cost_goal
from custom_components.mennekes_amtron_rtu.cost_goal import (
    Attempt,
    CostGoal,
    CostGoalObserver,
    energy_for,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.energy_goal import EnergyGoal
from custom_components.mennekes_amtron_rtu.device.registers import ENERGY_TOTAL


class Control:
    """The service: every positive target takes the meter as its baseline."""

    def __init__(self) -> None:
        self.energy_goal = EnergyGoal()
        self.meter = 100.0
        self.sets: list[float] = []
        self.fail: Exception | None = None

    async def async_set_energy_goal(self, value: float, *, expected: float | None = None) -> None:
        if self.fail is not None:
            raise self.fail
        if expected is not None and expected != self.energy_goal.target_kwh:
            raise HomeAssistantError(translation_domain="mennekes_amtron_rtu", translation_key="energy_goal_changed")
        self.sets.append(value)
        self.energy_goal.set_target(value, self.meter)


class Cost:
    def __init__(self, price: float | None) -> None:
        self.price = price

    def unit_price(self, _at: float) -> float | None:
        return self.price


def setup(tmp_path, price=0.30):
    hass = HomeAssistant(str(tmp_path))
    control, cost = Control(), Cost(price)
    entry = SimpleNamespace(entry_id="entry", runtime_data=SimpleNamespace(
        identity=SimpleNamespace(serial_number="123"), control=control, charging_cost=cost,
    ))
    entry.async_create_background_task = lambda _hass, target, name: asyncio.get_running_loop().create_task(target, name=name)
    return hass, entry, control, cost, CostGoalObserver(hass, entry)


def reading(control, meter=None, *, read=True):
    if meter is not None:
        control.meter = meter
    data = WallboxData({ENERGY_TOTAL.key: control.meter})
    data.energy_read = read
    return data


async def observe(observer, data):
    """One poll, and the hand-over it may have started."""
    await observer.async_observe(data)
    if observer._task is not None:
        await observer._task


def test_the_energy_keeps_within_the_amount_and_matches_the_service_rounding():
    assert energy_for(10, 0.30) == 33.3
    assert energy_for(0.9, 0.30) == 3.0
    assert energy_for(0.01, 0.30) == 0.1
    assert energy_for(5, 0) == energy_for(5, -0.1) == 1000.0
    assert energy_for(1000, 0.001) == 1000.0
    goal = EnergyGoal()
    goal.set_target(energy_for(0.09, 0.30), 0)
    assert goal.target_kwh == energy_for(0.09, 0.30) == 0.3


def test_an_amount_becomes_an_energy_target_and_follows_the_price(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_start()
        assert observer.status == "disabled"
        await observer.async_set_target(10)
        assert control.sets == [33.3]
        assert observer.status == "active"
        # The same price: nothing is handed over again.
        await observe(observer, reading(control, 110.0))
        await observe(observer, None)
        assert control.sets == [33.3]
        # A wobble below a tenth of a cent, and a new price within a minute
        # of the last hand-over, wait.
        cost.price = 0.3004
        clock[0] += 120
        await observe(observer, reading(control))
        cost.price = 0.50
        clock[0] -= 100
        await observe(observer, reading(control))
        assert control.sets == [33.3]
        # 10 kWh at 0.30 are spent; the remaining 7 buy 14 kWh at 0.50.
        clock[0] += 100
        await observe(observer, reading(control))
        assert control.sets == [33.3, 14.0]
        assert observer.goal.spent == pytest.approx(3.0)
        assert observer.goal.price == 0.50
        # A stale reading or an unknown price leaves the target as it is.
        cost.price = 0.60
        clock[0] += 120
        await observe(observer, reading(control, 112.0, read=False))
        cost.price = None
        await observe(observer, reading(control))
        assert observer.status == "price_unknown"
        assert control.sets == [33.3, 14.0]
        # The amount survives a restart of Home Assistant.
        restored = CostGoalObserver(hass, _entry)
        await restored.async_start()
        assert restored.goal.snapshot() == observer.goal.snapshot()
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_a_reached_target_is_left_alone_and_zero_ends_it(tmp_path):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        await observer.async_set_target(3)
        control.energy_goal.observe(110.0)
        assert observer.status == "reached"
        cost.price = 0.10
        await observe(observer, reading(control, 110.0))
        assert control.sets == [10.0]
        await observer.async_set_target(0)
        assert control.sets == [10.0, 0]
        assert observer.status == "disabled"
        await observe(observer, reading(control))
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_an_energy_target_set_by_hand_ends_the_amount_without_touching_it(tmp_path):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        await observer.async_set_target(3)
        await control.async_set_energy_goal(5.0)
        await observe(observer, reading(control))
        assert observer.target == 0
        cost.price = 0.5
        await observe(observer, reading(control))
        await observer.async_set_target(0)
        assert control.sets == [10.0, 5.0]
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_wrong_amounts_and_a_missing_price_are_refused(tmp_path):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        for value in (-1, 0.05, 1000.5, float("nan"), True, "3"):
            with pytest.raises(ServiceValidationError) as error:
                await observer.async_set_target(value)
            assert error.value.translation_key == "cost_goal_invalid"
        cost.price = None
        with pytest.raises(ServiceValidationError) as error:
            await observer.async_set_target(5)
        assert error.value.translation_key == "cost_goal_price_unknown"
        _entry.runtime_data.charging_cost = None
        with pytest.raises(ServiceValidationError):
            await observer.async_set_target(5)
        assert control.sets == []
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_a_refused_update_and_a_refused_save_are_tried_again(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        control.fail = HomeAssistantError("service down")
        await observe(observer, reading(control, 110.0))
        await observe(observer, reading(control))
        assert control.sets == [33.3]
        assert observer.goal.price == 0.30
        control.fail = None
        clock[0] += 61
        saves = []

        async def refuse(_goal):
            saves.append(1)
            raise OSError("disk full")

        monkeypatch.setattr(observer._store, "async_save", refuse)
        await observe(observer, reading(control))
        assert control.sets == [33.3, 14.0]
        await observe(observer, reading(control))
        # The attempt before sending, the result after; then the retry waits.
        assert saves == [1, 1]
        clock[0] += 61
        monkeypatch.undo()
        await observe(observer, reading(control))
        assert not observer._dirty
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_a_saved_amount_that_cannot_be_used_starts_without_one(tmp_path):
    async def run():
        hass, _entry, _control, _cost, observer = setup(tmp_path)

        async def broken():
            raise ValueError("not readable")

        observer._store.async_load = broken
        await observer.async_start()
        assert observer.target == 0
        await hass.async_stop(force=True)

    asyncio.run(run())


@pytest.mark.parametrize("raw", [
    None, {"target": 1}, {"target": 5, "spent": 0, "price": None, "energy_kwh": 1, "attempt": None},
    {"target": 5, "spent": float("nan"), "price": 0.3, "energy_kwh": 1, "attempt": None},
    {"target": 5, "spent": 0, "price": 0.3, "energy_kwh": 1, "attempt": [1, 0.3]},
    {"target": 5, "spent": 0, "price": 0.3, "energy_kwh": 1, "attempt": [1, "x", 3]},
    {"target": 5, "spent": 0, "price": 0.3, "energy_kwh": 2000, "attempt": None},
    {"target": True, "spent": 0, "price": 0.3, "energy_kwh": 1, "attempt": None},
])
def test_a_malformed_saved_amount_is_rejected(raw):
    with pytest.raises(ValueError, match=r"cost target|amount"):
        CostGoal.restore(raw)


def test_a_disabled_saved_amount_restores_empty():
    restored = CostGoal.restore({"target": 0, "spent": 9, "price": None, "energy_kwh": None, "attempt": None})
    assert restored.snapshot() == CostGoal().snapshot()


def test_without_a_baseline_or_with_nothing_left_nothing_is_handed_over(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(3)
        clock[0] += 120
        cost.price = 0.50
        # 9.9 kWh at 0.30 leave 0.03, less than one step at the new price.
        await observe(observer, reading(control, 109.9))
        fixed = SimpleNamespace(target_kwh=10.0, status="active", baseline_kwh=None)
        control.energy_goal = fixed
        await observe(observer, reading(control))
        assert control.sets == [10.0]
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_a_hand_over_whose_answer_was_lost_is_taken_when_the_service_shows_it(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        applied = control.async_set_energy_goal

        async def lost(value, **how):
            await applied(value, **how)
            raise HomeAssistantError("no answer")

        control.async_set_energy_goal = lost
        await observe(observer, reading(control, 110.0))
        assert observer.goal.energy_kwh == 33.3
        await observe(observer, reading(control))
        assert observer.goal.snapshot() == {"target": 10, "spent": pytest.approx(3.0), "price": 0.5, "energy_kwh": 14.0, "attempt": None}
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_a_poll_during_a_change_of_the_amount_leaves_it_alone(tmp_path):
    async def run():
        hass, _entry, control, _cost, observer = setup(tmp_path)
        await observer.async_set_target(10)
        async with observer._lock:
            control.energy_goal.set_target(5.0, 100.0)
            await observe(observer, reading(control))
        assert observer.target == 10
        # A hand-over that finds a newer amount, or a stop, does nothing.
        await observer._hand_over(CostGoal(), Attempt(1.0, 0.5, 100.0))
        control.energy_goal.observe(200.0)
        await observer._hand_over(observer.goal, Attempt(1.0, 0.5, 100.0))
        assert control.sets == [33.3]
        await observer.async_stop()
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_stopping_cancels_a_running_hand_over_and_saves_what_is_left(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        blocked = asyncio.Event()

        async def hang(_value, **_how):
            blocked.set()
            await asyncio.Event().wait()

        control.async_set_energy_goal = hang
        await observer.async_observe(reading(control, 110.0))
        await asyncio.wait_for(blocked.wait(), 2)
        observer._dirty = True
        await observer.async_stop()
        assert observer._task is None
        assert not observer._dirty
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_the_number_refuses_an_amount_without_charging_costs():
    from custom_components.mennekes_amtron_rtu import number

    async def run():
        with pytest.raises(ServiceValidationError):
            await number._set_cost_goal(SimpleNamespace(cost_goal=None), 5)
        seen = []

        async def set_target(value):
            seen.append(value)

        await number._set_cost_goal(SimpleNamespace(cost_goal=SimpleNamespace(async_set_target=set_target)), 5)
        assert seen == [5]

    asyncio.run(run())


def test_a_negative_price_earns_back_part_of_the_amount():
    restored = CostGoal.restore({"target": 5, "spent": -0.4, "price": -0.1, "energy_kwh": 1000, "attempt": None})
    assert restored.spent == -0.4


def test_a_lost_answer_seen_without_a_baseline_keeps_the_spent_part():
    observer = CostGoalObserver.__new__(CostGoalObserver)
    observer.goal = CostGoal(5, 1.5, 0.3, 10.0)
    assert observer._spent_until(None, since=100.0, price=0.3) == 1.5


def test_a_hand_over_interrupted_by_a_stop_is_recognized_after_the_restart(tmp_path, monkeypatch):
    async def run():
        hass, entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        applied = control.async_set_energy_goal
        arrived = asyncio.Event()

        async def arrive_then_hang(value, **how):
            await applied(value, **how)
            arrived.set()
            await asyncio.Event().wait()

        control.async_set_energy_goal = arrive_then_hang
        await observer.async_observe(reading(control, 110.0))
        await asyncio.wait_for(arrived.wait(), 2)
        await observer.async_stop()
        restarted = CostGoalObserver(hass, entry)
        await restarted.async_start()
        await observe(restarted, reading(control))
        assert restarted.goal.snapshot() == {
            "target": 10, "spent": pytest.approx(3.0), "price": 0.5, "energy_kwh": 14.0, "attempt": None,
        }
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_a_hand_over_never_replaces_a_target_set_meanwhile(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        await observer.async_observe(reading(control, 110.0))
        # The user's own command reaches the service first.
        control.energy_goal.set_target(5.0, 110.0)
        await observer._task
        assert control.energy_goal.target_kwh == 5.0
        await observe(observer, reading(control))
        assert observer.target == 0
        # Ending the amount leaves the user's target alone, too.
        await observer.async_set_target(3)
        control.energy_goal.set_target(7.0, 110.0)
        await observer.async_set_target(0)
        assert control.energy_goal.target_kwh == 7.0
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_an_attempt_that_did_not_arrive_is_forgotten_at_the_next_poll(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        control.fail = HomeAssistantError("service down")
        await observe(observer, reading(control, 110.0))
        assert observer.goal.attempt is not None
        control.fail = None
        cost.price = 0.30
        await observe(observer, reading(control))
        assert observer.goal.attempt is None
        # The same kWh set by hand later is the user's, not the amount's.
        await control.async_set_energy_goal(14.0)
        await observe(observer, reading(control))
        assert observer.target == 0
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_ending_the_amount_reports_any_other_refusal(tmp_path):
    async def run():
        hass, _entry, control, _cost, observer = setup(tmp_path)
        await observer.async_set_target(3)
        control.fail = HomeAssistantError("service down")
        with pytest.raises(HomeAssistantError):
            await observer.async_set_target(0)
        assert observer.target == 3
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_ending_the_amount_clears_an_attempt_the_service_already_holds(tmp_path, monkeypatch):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
        await observer.async_set_target(10)
        clock[0] += 120
        cost.price = 0.50
        applied = control.async_set_energy_goal

        async def lost(value, **how):
            await applied(value, **how)
            raise HomeAssistantError("no answer")

        control.async_set_energy_goal = lost
        await observe(observer, reading(control, 110.0))
        control.async_set_energy_goal = applied
        assert control.energy_goal.target_kwh == 14.0
        await observer.async_set_target(0)
        assert control.energy_goal.target_kwh == 0
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_ending_the_amount_when_another_target_came_in_between_keeps_it(tmp_path):
    async def run():
        hass, _entry, control, _cost, observer = setup(tmp_path)
        await observer.async_set_target(3)
        raced = control.async_set_energy_goal

        async def someone_first(value, **how):
            control.energy_goal.set_target(8.0, 100.0)
            await raced(value, **how)

        control.async_set_energy_goal = someone_first
        await observer.async_set_target(0)
        assert control.energy_goal.target_kwh == 8.0
        assert observer.target == 0
        await hass.async_stop(force=True)

    asyncio.run(run())
