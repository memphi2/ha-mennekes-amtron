"""Shared entity base for the MENNEKES AMTRON platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MennekesAmtronCoordinator
from .data import WallboxData
from .device.const import CONF_SERIAL_NUMBER, DOMAIN, MANUFACTURER
from .device.decode import RegisterValue
from .device.identity import display_identifier, model_name
from .entry_types import MennekesAmtronConfigEntry

if TYPE_CHECKING:
    from .data import MennekesAmtronRuntimeData


def entry_device_key(entry: MennekesAmtronConfigEntry) -> str:
    """Return the stable device key of one config entry.

    The serial number is read during the config flow and stored in the entry,
    so a re-created entry keeps its entities. Devices on a register layout
    older than v01.02 have no serial register; those fall back to the entry
    id, which is stable for the lifetime of the entry.
    """

    serial = entry.data.get(CONF_SERIAL_NUMBER)
    if isinstance(serial, str) and serial:
        return serial
    return entry.entry_id


def entry_entity_unique_id(entry: MennekesAmtronConfigEntry, key: str) -> str:
    """Return the unique id this integration gives one entity of an entry."""

    return f"{entry_device_key(entry)}_{key}"


# Entities that 0.9.0 folded into another entity's attributes. Their registry
# rows would otherwise linger on the device page as "no longer provided".
RETIRED_ENTITIES: dict[str, frozenset[str]] = {
    "sensor": frozenset({
        "evse_state", "cp_state", "error_code", "switched_phases",
        "voltage_l1", "voltage_l2", "voltage_l3", "power_l1", "power_l2", "power_l3",
        "session_max_current", "session_duration", "sessions_total",
        "modbus_layout_version", "downgrade_current", "max_current_house",
        "max_evse_current", "ems_fallback_behaviour", "ems_fallback_current",
        "grid_imbalance_threshold", "solar_min_current", "phase_switching_pause",
        "phase_rotation", "phase_switching_mode", "grid_phases_connected",
        "pending_change_until", "timed_goal_planned_start",
    }),
    "binary_sensor": frozenset({
        "heartbeat", "authorization_enabled", "grid_imbalance_enabled",
        "cable_lock_enabled", "phase_switch_capable",
    }),
}


@callback
def async_remove_retired_entities(hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
    """Drop the registry rows of entities this entry no longer provides."""

    registry = hass.data.get(er.DATA_REGISTRY)
    if registry is None:
        return
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        for key in RETIRED_ENTITIES.get(registered.domain, ()):
            if registered.unique_id == entry_entity_unique_id(entry, key):
                registry.async_remove(registered.entity_id)


def entry_device_info(entry: MennekesAmtronConfigEntry) -> DeviceInfo:
    """Return the device registry entry for one wallbox."""

    runtime_data: MennekesAmtronRuntimeData = entry.runtime_data
    identity = runtime_data.identity
    info = DeviceInfo(
        identifiers={(DOMAIN, entry_device_key(entry))},
        manufacturer=MANUFACTURER,
        model=model_name(identity.article_number),
        name=entry.title,
    )
    if identity.firmware_version:
        info["sw_version"] = identity.firmware_version
    if serial_number := display_identifier(identity.serial_number):
        info["serial_number"] = serial_number
    if article_number := display_identifier(identity.article_number):
        info["model_id"] = article_number
    return info


class MennekesAmtronEntity(CoordinatorEntity[MennekesAmtronCoordinator]):
    """Base entity bound to one wallbox and one register key."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: MennekesAmtronConfigEntry,
        coordinator: MennekesAmtronCoordinator,
        key: str,
        *,
        register_key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._register_key = register_key or key
        self._attr_unique_id = entry_entity_unique_id(entry, key)
        self._attr_device_info = entry_device_info(entry)

    @property
    def runtime_data(self) -> MennekesAmtronRuntimeData:
        """Return the runtime data of the owning config entry."""

        data: MennekesAmtronRuntimeData = self._entry.runtime_data
        return data

    @callback
    def _handle_coordinator_update(self) -> None:
        # A vehicle switch replaces observers in place. Publish only its
        # result, never the brief gap between the old and the new car.
        if self.runtime_data.vehicle_switching:
            return
        super()._handle_coordinator_update()

    @property
    def wallbox_data(self) -> WallboxData:
        """Return the newest device snapshot."""

        return self.coordinator.current_data()

    @property
    def register_value(self) -> RegisterValue:
        """Return this entity's register value from the newest snapshot."""

        return self.wallbox_data.get(self._register_key)

    @property
    def available(self) -> bool:
        """Return false while the register has no value in the snapshot."""

        if not super().available:
            return False
        return self.wallbox_data.has(self._register_key)


class MennekesAmtronControlEntity(MennekesAmtronEntity):
    """Keep control identities while following the current write permission."""

    @property
    def available(self) -> bool:
        return super().available and not self.runtime_data.control.read_only
