"""Binary-sensor platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
    AmtronBinarySensorEntityDescription,
)
from .coordinator import MennekesAmtronCoordinator
from .device.capabilities import capability_is_supported
from .device.registers import CP_STATE
from .device.vehicle import vehicle_connected
from .entity import MennekesAmtronEntity
from .entity_attributes import read_attributes
from .entry_types import MennekesAmtronConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the binary sensors this device supports."""

    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    async_add_entities(
        (AmtronVehicleConnectedSensor if description.key == "vehicle_connected"
         else AmtronBinarySensor)(entry, runtime_data.coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
        if capability_is_supported(
            identity.layout_version,
            identity.phase_options_hw,
            description.register_key,
        )
    )


class AmtronBinarySensor(MennekesAmtronEntity, BinarySensorEntity):
    """A binary sensor derived from one register."""

    entity_description: AmtronBinarySensorEntityDescription

    def __init__(
        self,
        entry: MennekesAmtronConfigEntry,
        coordinator: MennekesAmtronCoordinator,
        description: AmtronBinarySensorEntityDescription,
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            description.key,
            register_key=description.register_key,
        )
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the derived state of this binary sensor."""

        return self.entity_description.is_on_fn(self.register_value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The registers that detail this state."""

        return read_attributes(self.wallbox_data, self.entity_description.attributes)


class AmtronVehicleConnectedSensor(AmtronBinarySensor):
    """Use pilot state to retain presence during a wallbox fault."""

    @property
    def is_on(self) -> bool | None:
        return vehicle_connected(
            self.register_value, self.wallbox_data.get(CP_STATE.key),
            cp_supported=self.runtime_data.identity.layout_version >= CP_STATE.min_layout,
        )
