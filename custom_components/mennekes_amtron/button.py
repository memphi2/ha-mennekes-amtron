"""Button platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capabilities import capability_is_supported
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .registers import ERROR_CODE, SYSTEM_RESTART

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the buttons this device supports."""

    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    coordinator = runtime_data.coordinator
    entities: list[ButtonEntity] = [AmtronRecoverButton(entry, coordinator)]
    if capability_is_supported(
        identity.layout_version, identity.phase_options_hw, SYSTEM_RESTART.key
    ):
        entities.append(AmtronRestartButton(entry, coordinator))
    async_add_entities(entities)


class AmtronRecoverButton(MennekesAmtronEntity, ButtonEntity):
    """Run the documented recovery sequence after a heartbeat loss."""

    _attr_translation_key = "recover_from_error"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
        super().__init__(
            entry, coordinator, "recover_from_error", register_key=ERROR_CODE.key
        )

    async def async_press(self) -> None:
        """Write heartbeat, charging release and charging current, in order."""

        await self.runtime_data.control.async_recover_from_error()


class AmtronRestartButton(MennekesAmtronEntity, ButtonEntity):
    """Restart the wallbox through 0x0D19.

    The specification allows this only in the idle state, so the control
    layer refuses the press while an EV is connected or charging.
    """

    _attr_translation_key = "restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
        super().__init__(entry, coordinator, "restart", register_key="evse_state")

    async def async_press(self) -> None:
        """Restart the wallbox."""

        await self.runtime_data.control.async_restart()
