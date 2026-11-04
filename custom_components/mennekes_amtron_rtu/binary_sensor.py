"""Binary-sensor platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .binary_sensor_descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
    AmtronBinarySensorEntityDescription,
)
from .device.capabilities import capability_is_supported
from .device.heartbeat import HeartbeatTask
from .entity import MennekesAmtronEntity, entry_device_info, entry_entity_unique_id
from .entry_types import MennekesAmtronConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
else:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass

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
        AmtronBinarySensor(entry, runtime_data.coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
        if capability_is_supported(
            identity.layout_version,
            identity.phase_options_hw,
            description.register_key,
        )
    )
    if runtime_data.heartbeat is not None:
        async_add_entities([AmtronHeartbeatSensor(entry, runtime_data.heartbeat)])


class AmtronHeartbeatSensor(BinarySensorEntity):
    """Report missed heartbeats even when register polling has stopped."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "heartbeat"

    def __init__(self, entry: MennekesAmtronConfigEntry, heartbeat: HeartbeatTask) -> None:
        self._heartbeat = heartbeat
        self._attr_unique_id = entry_entity_unique_id(entry, "heartbeat")
        self._attr_device_info = entry_device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """On means a missed deadline or stopped task; off means healthy."""

        healthy = self._heartbeat.healthy
        return None if healthy is None else not healthy

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._heartbeat.async_add_listener(self.async_write_ha_state)
        )


class AmtronBinarySensor(MennekesAmtronEntity, BinarySensorEntity):
    """A binary sensor derived from one register."""

    entity_description: AmtronBinarySensorEntityDescription

    def __init__(
        self,
        entry: MennekesAmtronConfigEntry,
        coordinator: Any,
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
