"""Number platform for the MENNEKES AMTRON integration.

The charging-current limit is the one entity a user will touch daily, and it
is also the register with the dangerous zero. The entity therefore never
offers a value below 6 A: pausing has its own switch, and "no limitation"
has its own action.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CHARGING_CURRENT_DECIMALS,
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_STEP,
)
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .registers import CHARGING_CURRENT_EMS

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the charging-current limit."""

    async_add_entities(
        [AmtronChargingCurrentNumber(entry, entry.runtime_data.coordinator)]
    )


class AmtronChargingCurrentNumber(MennekesAmtronEntity, NumberEntity, RestoreEntity):
    """The EMS charging-current limit in 0x0302."""

    _attr_translation_key = "charging_current_limit"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = CHARGING_CURRENT_MINIMUM
    _attr_native_step = CHARGING_CURRENT_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
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
        """Return the active limit, or the stored setpoint while paused.

        While charging is paused the register holds the documented pause
        value of 1 A, which is below this entity's minimum. Showing the
        stored setpoint instead keeps the slider where the user left it.
        """

        value = self.register_value
        if isinstance(value, (int, float)) and value >= CHARGING_CURRENT_MINIMUM:
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
        if restored >= CHARGING_CURRENT_MINIMUM:
            control.charging_setpoint = min(restored, control.max_charging_current)

    async def async_set_native_value(self, value: float) -> None:
        """Set the charging-current limit."""

        await self.runtime_data.control.async_set_charging_current(value)
