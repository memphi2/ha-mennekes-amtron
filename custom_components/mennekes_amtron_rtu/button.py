"""Button platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MennekesAmtronCoordinator
from .device.capabilities import capability_is_supported
from .device.registers import ERROR_CODE, SYSTEM_RESTART
from .entity import MennekesAmtronControlEntity
from .entry_types import MennekesAmtronConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.button.const import ButtonDeviceClass
else:
    from homeassistant.components.button import ButtonDeviceClass

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
    entities: list[ButtonEntity] = [
        AmtronRecoverButton(entry, coordinator), AmtronCancelPendingButton(entry, coordinator),
    ]
    if capability_is_supported(
        identity.layout_version, identity.phase_options_hw, SYSTEM_RESTART.key
    ):
        entities.append(AmtronRestartButton(entry, coordinator))
    async_add_entities(entities)


class AmtronCancelPendingButton(MennekesAmtronControlEntity, ButtonEntity):
    """Remove a queued change without changing the current charging state."""

    _attr_translation_key = "cancel_pending_change"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        super().__init__(entry, coordinator, "cancel_pending_change")

    @property
    def available(self) -> bool:
        return self.runtime_data.control.can_cancel_pending

    async def async_press(self) -> None:
        await self.runtime_data.control.async_cancel_pending_change()


class AmtronRecoverButton(MennekesAmtronControlEntity, ButtonEntity):
    """Run the documented recovery sequence after a heartbeat loss."""

    _attr_translation_key = "recover_from_error"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        super().__init__(
            entry, coordinator, "recover_from_error", register_key=ERROR_CODE.key
        )

    async def async_press(self) -> None:
        """Write heartbeat, charging release and charging current, in order."""

        await self.runtime_data.control.async_recover_from_error()


class AmtronRestartButton(MennekesAmtronControlEntity, ButtonEntity):
    """Restart the wallbox through 0x0D19.

    The specification allows this only in the idle state, so the control
    layer refuses the press while an EV is connected or charging.
    """

    _attr_translation_key = "restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        super().__init__(entry, coordinator, "restart", register_key="evse_state")

    async def async_press(self) -> None:
        """Restart the wallbox."""

        await self.runtime_data.control.async_restart()
