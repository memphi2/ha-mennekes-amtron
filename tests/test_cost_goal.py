"""Home Assistant tells the service the price for a running amount."""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import cost_goal
from custom_components.mennekes_amtron_rtu.cost_goal import CostGoalObserver
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.energy_goal import (
    CostPart,
    EnergyGoal,
)


class Control:
    """The mirror of the service's energy target and the two commands."""

    def __init__(self) -> None:
        self.energy_goal = EnergyGoal()
        self.calls: list[tuple[str, object, object]] = []
        self.fail: Exception | None = None
        # The last price the service was told.
        self.cost_price: float | None = None
        self.cost_currency: str | None = "EUR"
        self.read_only = False
        self.supports_energy_goal = True

    async def async_set_cost_goal(self, amount, price, currency):
        self.cost_currency = currency
        self.calls.append(("goal", amount, price))
        if price is not None:
            self.cost_price = price
        if amount:
            self.energy_goal.set_target(33.3, 100.0)
            self.energy_goal.cost = CostPart(amount, 0.0, price)
        else:
            self.energy_goal.set_target(0, 0)

    async def async_set_cost_price(self, price, currency):
        self.calls.append(("price", None, price))
        if self.fail is not None:
            raise self.fail
        self.cost_currency = currency
        self.cost_price = price


class Cost:
    def __init__(self, price: float | None) -> None:
        self.price = price

    def unit_price(self, _at: float) -> float | None:
        return self.price


def setup(tmp_path, monkeypatch, price=0.30):
    hass = HomeAssistant(str(tmp_path))
    control, cost = Control(), Cost(price)
    entry = SimpleNamespace(entry_id="entry", runtime_data=SimpleNamespace(control=control, charging_cost=cost))
    entry.async_create_background_task = lambda _hass, target, name: asyncio.get_running_loop().create_task(target, name=name)
    clock = [1000.0]
    monkeypatch.setattr(cost_goal, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    return hass, control, cost, clock, CostGoalObserver(hass, entry)


async def observe(observer, data=None, *, failed=False):
    await observer.async_observe(None if failed else data or WallboxData({}))
    if observer._task is not None:
        await observer._task


def test_an_amount_is_armed_at_the_price_and_zero_ends_it(tmp_path, monkeypatch):
    async def run():
        hass, control, _cost, _clock, observer = setup(tmp_path, monkeypatch)
        assert (observer.target, observer.status) == (0.0, "disabled")
        await observer.async_set_target(10)
        assert control.calls == [("goal", 10, 0.30)]
        assert (observer.target, observer.status) == (10.0, "active")
        await observer.async_set_target(0)
        assert control.calls[-1] == ("goal", 0, 0.30)
        assert observer.target == 0.0
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_without_a_price_an_amount_is_refused_but_can_be_ended(tmp_path, monkeypatch):
    async def run():
        hass, control, cost, _clock, observer = setup(tmp_path, monkeypatch, price=None)
        with pytest.raises(ServiceValidationError):
            await observer.async_set_target(10)
        assert control.calls == []
        cost.price = 0.30
        await observer.async_set_target(10)
        cost.price = None
        assert observer.status == "price_unknown"
        await observe(observer)
        assert control.calls == [("goal", 10, 0.30)]
        await observer.async_set_target(0)
        assert control.calls[-1] == ("goal", 0, None)
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_a_new_price_is_told_after_the_poll_once_a_minute(tmp_path, monkeypatch):
    async def run():
        hass, control, cost, clock, observer = setup(tmp_path, monkeypatch)
        await observer.async_set_target(10)
        cost.price = 0.50
        await observe(observer)
        assert control.calls[-1] == ("goal", 10, 0.30)
        clock[0] += 66
        await observe(observer, failed=True)
        cost.price = 0.3005
        await observe(observer)
        assert len(control.calls) == 1
        cost.price = 0.50
        await observe(observer)
        assert control.calls[-1] == ("price", None, 0.50)
        await observe(observer)
        assert len(control.calls) == 2
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_the_price_is_told_without_an_amount_so_the_app_can_arm_one(tmp_path, monkeypatch):
    async def run():
        hass, control, cost, clock, observer = setup(tmp_path, monkeypatch)
        await observe(observer)
        assert control.calls == [("price", None, 0.30)]
        assert control.cost_price == 0.30
        clock[0] += 66
        await observe(observer)
        assert len(control.calls) == 1
        cost.price = None
        clock[0] += 66
        await observe(observer)
        assert len(control.calls) == 1
        # A service that would refuse it is not asked at every poll.
        cost.price = 0.50
        control.read_only = True
        await observe(observer)
        control.read_only, control.supports_energy_goal = False, False
        await observe(observer)
        assert len(control.calls) == 1
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_a_lost_price_is_told_again_later(tmp_path, monkeypatch, caplog):
    async def run():
        hass, control, cost, clock, observer = setup(tmp_path, monkeypatch)
        await observer.async_set_target(10)
        cost.price = 0.50
        control.fail = HomeAssistantError("timeout")
        clock[0] += 66
        with caplog.at_level(logging.WARNING):
            await observe(observer)
        assert "Cost target: new price not passed on" in caplog.text
        control.fail = None
        await observe(observer)
        assert len(control.calls) == 2
        clock[0] += 66
        await observe(observer)
        assert control.calls[-1] == ("price", None, 0.50)
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_stopping_cancels_a_price_on_its_way(tmp_path, monkeypatch):
    async def run():
        hass, control, cost, clock, observer = setup(tmp_path, monkeypatch)
        await observer.async_set_target(10)
        gate = asyncio.Event()

        async def slow(price):
            await gate.wait()

        control.async_set_cost_price = slow
        cost.price = 0.50
        clock[0] += 66
        await observer.async_observe(WallboxData({}))
        clock[0] += 66
        await observer.async_observe(WallboxData({}))
        task = observer._task
        await observer.async_stop()
        assert task.cancelled()
        await observer.async_stop()
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_the_number_passes_the_amount_on_and_refuses_without_costs():
    from custom_components.mennekes_amtron_rtu import number

    async def run():
        calls = []

        async def set_target(value):
            calls.append(value)

        runtime = SimpleNamespace(cost_goal=SimpleNamespace(async_set_target=set_target))
        await number._set_cost_goal(runtime, 12.5)
        assert calls == [12.5]
        with pytest.raises(ServiceValidationError):
            await number._set_cost_goal(SimpleNamespace(cost_goal=None), 1)

    asyncio.run(run())


def test_a_price_that_came_while_the_goal_was_stopped_is_told_again(tmp_path, monkeypatch):
    async def run():
        hass, control, cost, clock, observer = setup(tmp_path, monkeypatch)
        await observer.async_set_target(10)
        control.energy_goal.observe(None)
        cost.price = 0.50
        clock[0] += 66
        await observe(observer)
        # The service remembers it but a stopped goal keeps its own price.
        assert control.calls[-1] == ("price", None, 0.50)
        clock[0] += 66
        await observe(observer)
        assert len(control.calls) == 2
        control.energy_goal.observe(100.0)
        assert control.energy_goal.status == "active"
        await observe(observer)
        assert len(control.calls) == 3
        await hass.async_stop(force=True)
    asyncio.run(run())


def test_new_ha_currency_does_not_reprice_a_running_amount(tmp_path, monkeypatch):
    async def run():
        hass, control, _cost, _, observer = setup(tmp_path, monkeypatch)
        await observer.async_set_target(10)
        control.energy_goal.cost = CostPart(10, 0, 0.30, currency="EUR")
        hass.config.currency = "CHF"
        assert observer.status == "price_unknown"
        await observe(observer)
        assert control.calls == [("goal", 10, 0.30)]
        await hass.async_stop(force=True)

    asyncio.run(run())
