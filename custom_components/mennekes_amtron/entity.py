"""Shared entity base for the MENNEKES AMTRON platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL_NUMBER, DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import MennekesAmtronCoordinator
from .data import WallboxData
from .decode import RegisterValue
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


def entry_device_info(entry: MennekesAmtronConfigEntry) -> DeviceInfo:
    """Return the device registry entry for one wallbox."""

    runtime_data: MennekesAmtronRuntimeData = entry.runtime_data
    identity = runtime_data.identity
    info = DeviceInfo(
        identifiers={(DOMAIN, entry_device_key(entry))},
        manufacturer=MANUFACTURER,
        model=identity.article_number or DEFAULT_MODEL,
        name=entry.title,
    )
    if identity.firmware_version:
        info["sw_version"] = identity.firmware_version
    if identity.serial_number:
        info["serial_number"] = identity.serial_number
    if identity.article_number:
        info["model_id"] = identity.article_number
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
