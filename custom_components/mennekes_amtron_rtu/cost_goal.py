"""A charge target as an amount of money: Home Assistant brings the price.

The service holds the amount together with the energy target that carries
it, books the energy at each price and stops the charge itself, also while
Home Assistant is down. This side only tells it the price that applies now.
A lost or refused price is told again at a later poll: the service keeps
the last one, and telling it twice changes nothing.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import TYPE_CHECKING, Final

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .device.client_errors import AmtronBusError
from .device.data import WallboxData
from .device.energy_goal import (
    COST_GOAL_STEP,
    MAX_COST_GOAL,
    PRICE_INTERVAL_SECONDS,
    PRICE_TOLERANCE,
)
from .device.notice import FailureNotice
from .exceptions import service_validation_error

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

__all__ = ["COST_GOAL_STEP", "MAX_COST_GOAL", "CostGoalObserver"]

_LOGGER = logging.getLogger(__name__)
# A little longer than the service's own minute between two prices, so that
# the next one cannot arrive inside it and be dropped.
_RETRY_SECONDS: Final = PRICE_INTERVAL_SECONDS + 5.0


class CostGoalObserver:
    """Forward the price to the service; nothing is stored here."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._notice = FailureNotice(_LOGGER, "Cost target")
        self._task: asyncio.Task[None] | None = None
        self._told_at = -math.inf

    async def async_stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @property
    def target(self) -> float:
        cost = self._entry.runtime_data.control.energy_goal.cost
        return 0.0 if cost is None else cost.amount

    @property
    def status(self) -> str:
        """``disabled``, ``price_unknown`` or the state of the energy target."""
        goal = self._entry.runtime_data.control.energy_goal
        if goal.cost is None:
            return "disabled"
        if goal.status == "active" and (self._price() is None or goal.cost.currency != self._hass.config.currency):
            return "price_unknown"
        return goal.status

    def _price(self) -> float | None:
        cost = self._entry.runtime_data.charging_cost
        return None if cost is None else cost.unit_price(time.time())

    async def async_set_target(self, value: float) -> None:
        """Arm an amount from now on; zero ends it with its energy target."""
        price = self._price()
        if value and price is None:
            raise service_validation_error("cost_goal_price_unknown")
        await self._entry.runtime_data.control.async_set_cost_goal(value, price, self._hass.config.currency)
        self._told_at = time.monotonic()

    async def async_observe(self, data: WallboxData | None) -> None:
        """Tell the service a new price after the poll.

        Always, not only while an amount runs: the app can arm an amount at
        the last price the service knows.
        """
        if data is None:
            return
        control = self._entry.runtime_data.control
        if control.read_only or not control.supports_energy_goal:
            # The service would refuse it at every poll.
            return
        price = self._price()
        # A running amount shows the price it really has: one that came while
        # its goal was stopped is told again once the goal runs.
        goal = control.energy_goal
        if goal.cost is not None and goal.cost.currency != self._hass.config.currency:
            return
        running = goal.cost is not None and goal.status == "active"
        known = goal.cost.price if running and goal.cost is not None else control.cost_price
        if (
            price is None
            or (
                known is not None and abs(price - known) < PRICE_TOLERANCE
                # A changed currency is told like a changed price.
                and control.cost_currency == self._hass.config.currency
            )
            or time.monotonic() - self._told_at < _RETRY_SECONDS
            or (self._task is not None and not self._task.done())
        ):
            return
        self._told_at = time.monotonic()
        # A command refreshes the poll that is running now: send it after.
        self._task = self._entry.async_create_background_task(
            self._hass, self._tell(price, self._hass.config.currency), "mennekes_amtron_rtu_cost_price",
        )

    async def _tell(self, price: float, currency: str) -> None:
        try:
            await self._entry.runtime_data.control.async_set_cost_price(price, currency)
        except (AmtronBusError, HomeAssistantError):
            self._notice.failed("new price not passed on", "retrying at a later poll")
        else:
            self._notice.recovered("price passed on again")
