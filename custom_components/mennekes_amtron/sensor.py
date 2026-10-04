"""Sensor platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .capabilities import capability_is_supported
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .registers import REGISTERS_BY_KEY
from .sensor_descriptions import (
    SENSOR_DESCRIPTIONS,
    AmtronSensorEntityDescription,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the sensors this device supports."""

    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    entities: list[SensorEntity] = []
    for description in SENSOR_DESCRIPTIONS:
        if not capability_is_supported(
            identity.layout_version,
            identity.phase_options_hw,
            description.register_key,
        ):
            continue
        if description.key == "error_code":
            entities.append(
                AmtronErrorCodeSensor(entry, runtime_data.coordinator, description)
            )
        else:
            entities.append(
                AmtronSensor(entry, runtime_data.coordinator, description)
            )
    async_add_entities(entities)


class AmtronSensor(MennekesAmtronEntity, SensorEntity):
    """A sensor reading one register."""

    entity_description: AmtronSensorEntityDescription

    def __init__(
        self,
        entry: MennekesAmtronConfigEntry,
        coordinator: Any,
        description: AmtronSensorEntityDescription,
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            description.key,
            register_key=description.register_key,
        )
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the decoded register value as a Home Assistant state."""

        value = self.register_value
        description = self.entity_description
        if description.value_fn is not None:
            return description.value_fn(value)
        if description.enum_class is not None:
            return _enum_state(description, value)
        if isinstance(value, (int, float, str)):
            return value
        return None


class AmtronErrorCodeSensor(AmtronSensor):
    """The active error code, reported as a documented category.

    The register is a plain number and the specification documents only a few
    of them, so the raw code stays visible as an attribute instead of being
    squeezed into an invented option name.
    """

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Return the raw error code of the wallbox."""

        value = self.register_value
        if not isinstance(value, int):
            return None
        return {"code": value}


def _enum_state(
    description: AmtronSensorEntityDescription, value: Any
) -> StateType:
    """Return the state option of an enum register value.

    The register map owns the mapping, so an undocumented value becomes no
    state at all instead of an option Home Assistant does not know.
    """

    spec = REGISTERS_BY_KEY.get(description.register_key)
    if spec is None or spec.enum_map is None or not isinstance(value, int):
        return None
    return spec.enum_map.get(value)
