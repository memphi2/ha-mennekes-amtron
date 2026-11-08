"""Number platform for the MENNEKES AMTRON integration.

Zero disables the EMS limit; 6 A and above set a limit. The invalid band in
between is rejected by the control layer. Pausing has its own switch.
"""

from __future__ import annotations

import math

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import MennekesAmtronCoordinator
from .device.const import (
    CHARGING_CURRENT_DECIMALS,
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_STEP,
    CHARGING_CURRENT_UNLIMITED,
)
from .device.energy_goal import ENERGY_GOAL_STEP_KWH, MAX_ENERGY_GOAL_KWH
from .device.registers import CHARGING_CURRENT_EMS, ENERGY_TOTAL, EVSE_STATE
from .entity import MennekesAmtronControlEntity
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .vehicle_profiles import vehicle_options, vehicle_soc_is_configured

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the charging-current limit."""

    entities: list[NumberEntity] = [
        AmtronChargingCurrentNumber(entry, entry.runtime_data.coordinator)
    ]
    if entry.runtime_data.identity.layout_version >= ENERGY_TOTAL.min_layout:
        entities.append(AmtronEnergyGoalNumber(entry, entry.runtime_data.coordinator))
    if vehicle_soc_is_configured(vehicle_options(entry.options)):
        entities.append(AmtronSocGoalNumber(entry, entry.runtime_data.coordinator))
    async_add_entities(entities)


class AmtronSocGoalNumber(MennekesAmtronControlEntity, NumberEntity):
    """An optional percentage ceiling using the vehicle's reported charge."""

    _attr_translation_key = "soc_goal"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator,
    ) -> None:
        super().__init__(entry, coordinator, "soc_goal", register_key=EVSE_STATE.key)

    @property
    def available(self) -> bool:
        return (
            super().available and self.runtime_data.control.supports_soc_goal
            and self.runtime_data.vehicle_soc is not None
        )

    @property
    def native_max_value(self) -> float:
        cap = self.runtime_data.control.soc_goal.maximum_percent
        return 100.0 if cap is None else cap

    @property
    def native_value(self) -> float:
        return self.runtime_data.control.soc_goal.target_percent

    async def async_set_native_value(self, value: float) -> None:
        observer = self.runtime_data.vehicle_soc
        if observer is None:
            raise service_validation_error("soc_goal_unavailable")
        await observer.async_set_target(value)


class AmtronEnergyGoalNumber(MennekesAmtronControlEntity, NumberEntity):
    """Limit additional energy without starting or resuming a charge."""

    _attr_translation_key = "energy_goal"
    _attr_device_class = NumberDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_native_min_value = 0.0
    _attr_native_max_value = MAX_ENERGY_GOAL_KWH
    _attr_native_step = ENERGY_GOAL_STEP_KWH
    _attr_mode = NumberMode.BOX

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator,
    ) -> None:
        super().__init__(entry, coordinator, "energy_goal", register_key=EVSE_STATE.key)

    @property
    def available(self) -> bool:
        return super().available and self.runtime_data.control.supports_energy_goal

    @property
    def native_value(self) -> float:
        return self.runtime_data.control.energy_goal.target_kwh

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime_data.control.async_set_energy_goal(value)


class AmtronChargingCurrentNumber(MennekesAmtronControlEntity, NumberEntity, RestoreEntity):
    """The EMS charging-current limit in 0x0302."""

    _attr_translation_key = "charging_current_limit"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = CHARGING_CURRENT_UNLIMITED
    _attr_native_step = CHARGING_CURRENT_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            "charging_current_limit",
            register_key=CHARGING_CURRENT_EMS.key,
        )

    @property
    def native_max_value(self) -> float:
        """Return the highest current the wallbox and the options allow."""

        return self.runtime_data.control.max_charging_current

    @property
    def native_value(self) -> float | None:
        """Return the active limit, 0 when unlimited, or the paused setpoint.

        While charging is paused the register holds the documented pause
        value of 1 A. Showing the stored setpoint instead keeps the current
        input where the user left it, including 0 for no limitation.
        """

        value = self.register_value
        if isinstance(value, (int, float)) and (
            value == CHARGING_CURRENT_UNLIMITED or value >= CHARGING_CURRENT_MINIMUM
        ):
            # The register is a float32, so a 10 A limit comes back as
            # 9.999999... The entity cannot offer more resolution than its own
            # step, and recording more of it only costs database rows.
            return round(float(value), CHARGING_CURRENT_DECIMALS)
        return self.runtime_data.control.charging_setpoint

    async def async_added_to_hass(self) -> None:
        """Restore the setpoint a previous Home Assistant run was using."""

        await super().async_added_to_hass()
        control = self.runtime_data.control
        if control.charging_setpoint is not None:
            return
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        try:
            restored = float(last_state.state)
        except (TypeError, ValueError):
            return
        if math.isfinite(restored) and (
            restored == CHARGING_CURRENT_UNLIMITED or restored >= CHARGING_CURRENT_MINIMUM
        ):
            control.charging_setpoint = min(restored, control.max_charging_current)

    async def async_set_native_value(self, value: float) -> None:
        """Set a limit or explicitly disable it by entering zero."""

        await self.runtime_data.control.async_set_charging_current(
            value, allow_unlimited=value == CHARGING_CURRENT_UNLIMITED
        )
