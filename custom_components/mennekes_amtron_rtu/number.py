"""Numbers: the charging current and the two charge targets.

Zero disables the current limit; 6 A and above set one. The band in between
is rejected by the service. Pausing has its own switch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.const import (
    CHARGING_CURRENT_DECIMALS,
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_STEP,
    CHARGING_CURRENT_UNLIMITED,
)
from .device.energy_goal import ENERGY_GOAL_STEP_KWH, MAX_ENERGY_GOAL_KWH
from .device.registers import CHARGING_CURRENT_EMS, ENERGY_TOTAL
from .entity import AmtronEntity, Projection, Runtime, wanted
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .vehicle_features import SOC

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AmtronNumberDescription(Projection, NumberEntityDescription):
    value: Callable[[Runtime], float | None]
    set_value: Callable[[Runtime, float], Awaitable[None]]
    maximum: Callable[[Runtime], float] | None = None


def _current(runtime: Runtime) -> float | None:
    """The active limit, 0 when unlimited, or the setpoint a pause keeps.

    While paused the register holds the pause value of 1 A; the setpoint
    keeps the input where the user left it.
    """
    value = runtime.coordinator.current_data().get(CHARGING_CURRENT_EMS.key)
    if isinstance(value, (int, float)) and (
        value == CHARGING_CURRENT_UNLIMITED or value >= CHARGING_CURRENT_MINIMUM
    ):
        # A float32 reads 10 A back as 9.999999; the step is the resolution.
        return round(float(value), CHARGING_CURRENT_DECIMALS)
    return runtime.control.charging_setpoint


async def _set_vehicle_target(runtime: Runtime, value: float) -> None:
    if runtime.vehicle_soc is None:
        raise service_validation_error("soc_goal_unavailable")
    await runtime.vehicle_soc.async_set_target(value)


def _vehicle_maximum(runtime: Runtime) -> float:
    cap = runtime.control.soc_goal.maximum_percent
    return 100.0 if cap is None else cap


NUMBERS: tuple[AmtronNumberDescription, ...] = (
    AmtronNumberDescription(
        key="charging_current_limit", register=CHARGING_CURRENT_EMS.key, writes=True,
        device_class=NumberDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_min_value=CHARGING_CURRENT_UNLIMITED, native_step=CHARGING_CURRENT_STEP,
        mode=NumberMode.SLIDER, value=_current,
        maximum=lambda runtime: runtime.control.max_charging_current,
        set_value=lambda runtime, value: runtime.control.async_set_charging_current(
            value, allow_unlimited=value == CHARGING_CURRENT_UNLIMITED,
        ),
    ),
    # Limits additional energy; it never starts or resumes a charge.
    AmtronNumberDescription(
        key="energy_goal", capability=ENERGY_TOTAL.key, register="evse_state", writes=True,
        device_class=NumberDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        native_min_value=0.0, native_max_value=MAX_ENERGY_GOAL_KWH,
        native_step=ENERGY_GOAL_STEP_KWH, mode=NumberMode.BOX,
        ready=lambda runtime: runtime.control.supports_energy_goal,
        value=lambda runtime: runtime.control.energy_goal.target_kwh,
        set_value=lambda runtime, value: runtime.control.async_set_energy_goal(value),
    ),
    AmtronNumberDescription(
        key="soc_goal", writes=True, feature=lambda features: SOC in features,
        native_unit_of_measurement=PERCENTAGE, native_min_value=0.0, native_step=1.0,
        mode=NumberMode.SLIDER,
        ready=lambda runtime: (
            runtime.control.supports_soc_goal
            and runtime.vehicle_soc is not None and runtime.vehicle_soc.source is not None
        ),
        value=lambda runtime: runtime.control.soc_goal.target_percent,
        maximum=_vehicle_maximum, set_value=_set_vehicle_target,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(AmtronNumber(entry, description) for description in wanted(entry, NUMBERS))


class AmtronNumber(AmtronEntity, NumberEntity):
    entity_description: AmtronNumberDescription

    @property
    def native_max_value(self) -> float:
        maximum = self.entity_description.maximum
        if maximum is not None:
            return maximum(self.runtime)
        return self.entity_description.native_max_value or 100.0

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.runtime)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_value(self.runtime, value)
