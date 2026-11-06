"""Translate the shared driver's command errors for Home Assistant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from functools import wraps
from typing import Any

from .device.control import AmtronControl as DeviceControl
from .device.control import ControlOptions as ControlOptions
from .device.enums import RequestedPhases, SolarChargingMode
from .device.errors import DeviceError, DeviceValidationError
from .exceptions import home_assistant_error, service_validation_error


def ha_command[**P, T](
    method: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Preserve signatures while adapting errors at the HA boundary."""

    @wraps(method)
    async def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await method(*args, **kwargs)
        except DeviceError as err:
            factory = (
                service_validation_error
                if isinstance(err, DeviceValidationError)
                else home_assistant_error
            )
            raise factory(
                err.translation_key, err.translation_placeholders
            ) from err.__cause__

    return call


class AmtronControl(DeviceControl):
    """The direct serial backend, with translated Home Assistant errors."""

    @ha_command
    async def async_set_charging_current(
        self, value: float, *, allow_unlimited: bool = False
    ) -> None:
        await super().async_set_charging_current(value, allow_unlimited=allow_unlimited)

    @ha_command
    async def async_set_energy_goal(self, value: float) -> None:
        await super().async_set_energy_goal(value)

    @ha_command
    async def async_configure_soc(self, source: str | None) -> None:
        await super().async_configure_soc(source)

    @ha_command
    async def async_set_soc_goal(
        self, value: float, *, source: str, soc: float | None = None,
        maximum: float | None = None,
    ) -> None:
        await super().async_set_soc_goal(value, source=source, soc=soc, maximum=maximum)

    @ha_command
    async def async_observe_soc(
        self, soc: float | None, maximum: float | None = None, *, source: str,
    ) -> None:
        await super().async_observe_soc(soc, maximum, source=source)

    @ha_command
    async def async_pause_charging(self) -> None:
        await super().async_pause_charging()

    @ha_command
    async def async_set_timed_current(
        self, value: float | None, *, deadline: datetime, owner: str,
    ) -> None:
        await super().async_set_timed_current(value, deadline=deadline, owner=owner)

    @ha_command
    async def async_cancel_timed_current(self, owner: str) -> None:
        await super().async_cancel_timed_current(owner)

    @ha_command
    async def async_cancel_pending_change(self) -> None:
        await super().async_cancel_pending_change()

    @ha_command
    async def async_resume_charging(self, *, current_cap: float | None = None) -> None:
        await super().async_resume_charging(current_cap=current_cap)

    @ha_command
    async def async_set_charging_release(self, released: bool) -> None:
        await super().async_set_charging_release(released)

    @ha_command
    async def async_set_lock_evse(self, locked: bool) -> None:
        await super().async_set_lock_evse(locked)

    @ha_command
    async def async_set_solar_charging_mode(self, mode: SolarChargingMode) -> None:
        await super().async_set_solar_charging_mode(mode)

    @ha_command
    async def async_set_requested_phases(self, phases: RequestedPhases) -> None:
        await super().async_set_requested_phases(phases)

    @ha_command
    async def async_recover_from_error(self) -> bool:
        return await super().async_recover_from_error()

    @ha_command
    async def async_restart(self) -> None:
        await super().async_restart()
