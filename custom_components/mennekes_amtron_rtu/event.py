"""Discrete command outcomes and wallbox changes, without recurring history writes."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.components.logbook import async_log_entry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MennekesAmtronCoordinator
from .data import WallboxData
from .device.command_events import EVENT_TYPES, CommandEvent, event_type
from .device.const import DOMAIN
from .device.registers import CP_STATE
from .device.wallbox_events import EVENT_TYPES as WALLBOX_EVENT_TYPES
from .device.wallbox_events import WallboxChanges
from .entity import entry_device_info, entry_entity_unique_id
from .entry_types import MennekesAmtronConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the result and change entities, including in read-only installations."""
    async_add_entities([AmtronControlResultEvent(entry), AmtronWallboxEvent(entry)])


class AmtronWallboxEvent(CoordinatorEntity[MennekesAmtronCoordinator], EventEntity):
    """Observe wallbox changes and publish separate, attributed command activity."""

    _attr_has_entity_name = True
    _attr_translation_key = "wallbox_event"

    def __init__(self, entry: MennekesAmtronConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._attr_event_types = list(WALLBOX_EVENT_TYPES)
        self._attr_unique_id = entry_entity_unique_id(entry, "wallbox_event")
        self._attr_device_info = entry_device_info(entry)
        self._feed = entry.runtime_data.control.events
        self._activity_translations: dict[str, str] = {}
        self._changes = WallboxChanges(
            cp_supported=entry.runtime_data.identity.layout_version >= CP_STATE.min_layout,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The reading present when the entity appears is the baseline, so a
        # reload reports nothing that happened before it.
        self._changes.observe(self._reading())
        self._activity_translations = await async_get_translations(
            self.hass, self.hass.config.language, "entity", {DOMAIN},
        )
        self.async_on_remove(self._feed.listen(self._log_activity))

    def _reading(self) -> WallboxData | None:
        return self.coordinator.data if self.coordinator.last_update_success else None

    @callback
    def _handle_coordinator_update(self) -> None:
        events = self._changes.observe(self._reading())
        for event in events:
            self._trigger_event(event.type, dict(event.attributes))
            self.async_write_ha_state()
        if not events:
            super()._handle_coordinator_update()

    def _activity_label(self, attribute: str, value: str) -> str:
        key = f"component.{DOMAIN}.entity.event.control_result.state_attributes.{attribute}.state.{value}"
        return self._activity_translations.get(key, value)

    def _log_activity(self, event: CommandEvent) -> None:
        # Dragging the current slider can supersede many unsent values.
        # The final outcome is useful activity; the raw feed retains details.
        if event.action == "set_current" and event.outcome == "replaced":
            return
        action = self._activity_label("event_type", event_type(event.action, event.outcome))
        value_label = self._activity_translations.get(
            f"component.{DOMAIN}.entity.event.control_result.state_attributes.requested_value.name", "Requested",
        )
        value = f" ({value_label}: {event.requested_value:g} A)" if event.requested_value is not None and event.action in ("set_current", "resume") else ""
        if value and event.requested_value == 0:
            value = f" ({self._activity_label("requested_value", "0")})"
        source = self._activity_label("source", event.source)
        reason = "" if event.reason == "completed" else f". {self._activity_label("reason", event.reason)}"
        async_log_entry(self.hass, "Mennekes Amtron", f"{source}: {action}{value}{reason}.", domain=DOMAIN, entity_id=self.entity_id)


class AmtronControlResultEvent(CoordinatorEntity[MennekesAmtronCoordinator], EventEntity):
    """Track commands accepted by the controller, not inferred charging starts."""

    _attr_has_entity_name = True
    _attr_translation_key = "control_result"
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry: MennekesAmtronConfigEntry) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self._attr_event_types = list(EVENT_TYPES)
        self._feed = entry.runtime_data.control.events
        self._attr_unique_id = entry_entity_unique_id(entry, "control_result")
        self._attr_device_info = entry_device_info(entry)

    @property
    def available(self) -> bool:
        """Wait for the service's controller to initialize its event feed."""
        return super().available and self._feed.supported

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._feed.listen(self._handle_event))

    @callback
    def _handle_event(self, event: CommandEvent) -> None:
        self._trigger_event(
            event_type(event.action, event.outcome),
            {"action": event.action, "result": event.outcome, "reason": event.reason,
             "source": event.source, "occurred_at": event.occurred_at,
             "requested_value": event.requested_value},
        )
        self.async_write_ha_state()
