"""A charge target in money, carried by the service's energy target."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import cost_goal
from custom_components.mennekes_amtron_rtu.cost_goal import (
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

    async def async_set_energy_goal(self, value: float) -> None:
        if self.fail is not None:
            raise self.fail
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
    return hass, entry, control, cost, CostGoalObserver(hass, entry)


def reading(control, meter=None, *, read=True):
    if meter is not None:
        control.meter = meter
    data = WallboxData({ENERGY_TOTAL.key: control.meter})
    data.energy_read = read
    return data


def test_the_energy_keeps_within_the_amount_and_matches_the_service_rounding():
    assert energy_for(10, 0.30) == 33.3
    assert energy_for(0.9, 0.30) == 3.0
    assert energy_for(0.01, 0.30) == 0.1
    assert energy_for(5, 0) == energy_for(5, -0.1) == 1000.0
    assert energy_for(1000, 0.001) == 1000.0
    goal = EnergyGoal()
    goal.set_target(energy_for(0.09, 0.30), 0)
    assert goal.target_kwh == energy_for(0.09, 0.30) == 0.3


def test_an_amount_becomes_an_energy_target_and_follows_the_price(tmp_path):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        await observer.async_start()
        assert observer.status == "disabled"
        await observer.async_set_target(10)
        assert control.sets == [33.3]
        assert observer.status == "active"
        # The same price: nothing is handed over again.
        await observer.async_observe(reading(control, 110.0))
        await observer.async_observe(None)
        assert control.sets == [33.3]
        # 10 kWh at 0.30 are spent; the remaining 7 buy 14 kWh at 0.50.
        cost.price = 0.50
        await observer.async_observe(reading(control))
        assert control.sets == [33.3, 14.0]
        assert observer.goal.spent == pytest.approx(3.0)
        assert observer.goal.price == 0.50
        # A stale reading or an unknown price leaves the target as it is.
        cost.price = 0.60
        await observer.async_observe(reading(control, 112.0, read=False))
        cost.price = None
        await observer.async_observe(reading(control))
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
        await observer.async_observe(reading(control, 110.0))
        assert control.sets == [10.0]
        await observer.async_set_target(0)
        assert control.sets == [10.0, 0]
        assert observer.status == "disabled"
        await observer.async_observe(reading(control))
        await hass.async_stop(force=True)

    asyncio.run(run())


def test_an_energy_target_set_by_hand_ends_the_amount_without_touching_it(tmp_path):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        await observer.async_set_target(3)
        await control.async_set_energy_goal(5.0)
        await observer.async_observe(reading(control))
        assert observer.target == 0
        cost.price = 0.5
        await observer.async_observe(reading(control))
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
        monkeypatch.setattr(cost_goal.time, "monotonic", lambda: clock[0])
        await observer.async_set_target(10)
        cost.price = 0.50
        control.fail = HomeAssistantError("service down")
        await observer.async_observe(reading(control, 110.0))
        await observer.async_observe(reading(control))
        assert control.sets == [33.3]
        assert observer.goal.price == 0.30
        control.fail = None
        clock[0] += 61
        saves = []

        async def refuse(_goal):
            saves.append(1)
            raise OSError("disk full")

        monkeypatch.setattr(observer._store, "async_save", refuse)
        await observer.async_observe(reading(control))
        assert control.sets == [33.3, 14.0]
        await observer.async_observe(reading(control))
        assert saves == [1]
        clock[0] += 61
        monkeypatch.undo()
        await observer.async_observe(reading(control))
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
    None, {"target": 1}, {"target": 5, "spent": 0, "price": None, "energy_kwh": 1},
    {"target": 5, "spent": -1, "price": 0.3, "energy_kwh": 1},
    {"target": 5, "spent": 0, "price": 0.3, "energy_kwh": 2000},
    {"target": True, "spent": 0, "price": 0.3, "energy_kwh": 1},
])
def test_a_malformed_saved_amount_is_rejected(raw):
    with pytest.raises(ValueError, match=r"cost target|amount"):
        CostGoal.restore(raw)


def test_a_disabled_saved_amount_restores_empty():
    restored = CostGoal.restore({"target": 0, "spent": 9, "price": None, "energy_kwh": None})
    assert restored.snapshot() == CostGoal().snapshot()


def test_without_a_baseline_the_spent_amount_stays_as_saved(tmp_path):
    async def run():
        hass, _entry, control, cost, observer = setup(tmp_path)
        await observer.async_set_target(10)
        fixed = SimpleNamespace(target_kwh=33.3, status="active", baseline_kwh=None)

        async def set_goal(value):
            control.sets.append(value)
            fixed.target_kwh = value

        control.energy_goal, control.async_set_energy_goal = fixed, set_goal
        cost.price = 0.50
        await observer.async_observe(reading(control, 110.0))
        assert control.sets == [33.3, 20.0]
        assert observer.goal.spent == 0
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
