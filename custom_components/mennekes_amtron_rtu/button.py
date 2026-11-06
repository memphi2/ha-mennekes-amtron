"""Button platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .charge_loss_const import LOSS_SOURCE_SENSOR
from .coordinator import MennekesAmtronCoordinator
from .device.capabilities import capability_is_supported
from .device.registers import ERROR_CODE, SYSTEM_RESTART
from .entity import MennekesAmtronControlEntity, MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .remote import CONF_SERVER_URL
from .timed_goal_const import CONF_TIMED_BAVARIAN_DEVICE, CONF_TIMED_CALENDAR_ENTITY

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
    if runtime_data.charge_loss is not None and runtime_data.charge_loss.mode == LOSS_SOURCE_SENSOR:
        entities.append(AmtronAssignChargeButton(entry, coordinator))
    if runtime_data.timed_goal is not None and entry.options.get(CONF_TIMED_CALENDAR_ENTITY):
        entities.append(AmtronNextDepartureButton(entry, coordinator))
    if runtime_data.timed_goal is not None and entry.options.get(CONF_TIMED_BAVARIAN_DEVICE):
        entities.append(AmtronBmwDepartureButton(entry, coordinator))
    if runtime_data.timed_goal is not None:
        entities.append(AmtronClearDepartureButton(entry, coordinator))
    if runtime_data.control.read_only and CONF_SERVER_URL not in entry.data:
        entities = [entity for entity in entities if isinstance(entity, AmtronAssignChargeButton)]
    async_add_entities(entities)


class AmtronNextDepartureButton(MennekesAmtronControlEntity, ButtonEntity):
    """Choose exactly one upcoming calendar event as the next trip."""

    _attr_translation_key = "timed_next_calendar_event"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        super().__init__(entry, coordinator, "timed_next_calendar_event")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and not self.runtime_data.control.read_only

    async def async_press(self) -> None:
        observer = self.runtime_data.timed_goal
        if observer is not None:
            await observer.async_use_next_calendar_event()


class AmtronBmwDepartureButton(AmtronNextDepartureButton):
    """Copy one active BMW climate departure without enabling recurrence."""

    _attr_translation_key = "timed_bmw_departure"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        MennekesAmtronControlEntity.__init__(self, entry, coordinator, "timed_bmw_departure")

    async def async_press(self) -> None:
        observer = self.runtime_data.timed_goal
        if observer is not None:
            await observer.async_use_bmw_departure()


class AmtronClearDepartureButton(MennekesAmtronControlEntity, ButtonEntity):
    """Cancel the selected one-off trip without changing the SoC ceiling."""

    _attr_translation_key = "timed_clear_departure"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
    ) -> None:
        super().__init__(entry, coordinator, "timed_clear_departure")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and not self.runtime_data.control.read_only

    async def async_press(self) -> None:
        observer = self.runtime_data.timed_goal
        if observer is not None:
            await observer.async_set_deadline(None)


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


class AmtronAssignChargeButton(MennekesAmtronEntity, ButtonEntity):
    """Explicitly pair external battery energy; no physical write permission needed."""

    _attr_translation_key = "assign_vehicle_charge"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator) -> None:
        super().__init__(entry, coordinator, "assign_vehicle_charge")

    @property
    def available(self) -> bool:
        observer = self.runtime_data.charge_loss
        return observer is not None and observer.can_assign

    async def async_press(self) -> None:
        observer = self.runtime_data.charge_loss
        if observer is None:
            raise service_validation_error("loss_unavailable")
        await observer.async_assign()
