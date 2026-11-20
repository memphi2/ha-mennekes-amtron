"""Shared entity base: every entity shows one field of the service's state.

A platform is a table of descriptions. A description says what the entity
shows, when it exists and when it is available; the classes here and in the
platforms contain no knowledge of single entities.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL_NUMBER, MANUFACTURER
from .coordinator import ServiceCoordinator
from .data import WallboxData
from .device.capabilities import capability_is_supported
from .device.const import DOMAIN
from .device.identity import display_identifier, model_name
from .entry_types import MennekesAmtronConfigEntry
from .vehicle_features import vehicle_features

if TYPE_CHECKING:
    from .data import MennekesAmtronRuntimeData

type Runtime = MennekesAmtronRuntimeData
type Attributes = Callable[[Runtime], dict[str, Any]]


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

    identity = entry.runtime_data.identity
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


def _always(_runtime: Runtime) -> bool:
    return True


def _no_feature(_features: frozenset[str]) -> bool:
    return True


@dataclass(frozen=True, kw_only=True)
class Projection:
    """What every description says beyond Home Assistant's own fields.

    ``register`` names the register the entity depends on: an entity for a
    register the wallbox lacks is not created, and one whose register has no
    value is unavailable. ``writes`` marks a control, unavailable while the
    service allows no writes. ``exists`` and ``feature`` decide once, at setup;
    ``ready`` decides on every update.
    """

    register: str | None = None
    # A register the wallbox must have although it cannot be read back.
    capability: str | None = None
    writes: bool = False
    # Usable from the service's last state, also while the wallbox is down.
    offline: bool = False
    exists: Callable[[Runtime], bool] = _always
    feature: Callable[[frozenset[str]], bool] = _no_feature
    ready: Callable[[Runtime], bool] = _always
    attributes: Attributes | None = None


def wanted[D: Projection](
    entry: MennekesAmtronConfigEntry, descriptions: tuple[D, ...],
) -> list[D]:
    """The descriptions this wallbox and its configured vehicles call for."""
    runtime = entry.runtime_data
    identity = runtime.identity
    features = vehicle_features(entry)
    return [
        description for description in descriptions
        if (
            (needed := description.capability or description.register) is None
            or capability_is_supported(identity.layout_version, identity.phase_options_hw, needed)
        )
        and description.feature(features) and description.exists(runtime)
    ]


class AmtronEntity(CoordinatorEntity[ServiceCoordinator]):
    """One entity of one wallbox, described by a table row."""

    _attr_has_entity_name = True
    _projection: Projection

    def __init__(
        self, entry: MennekesAmtronConfigEntry, description: EntityDescription,
    ) -> None:
        super().__init__(entry.runtime_data.coordinator)
        if not isinstance(description, Projection):
            raise TypeError("an entity description must be a Projection")
        self._entry = entry
        self._projection = description
        self.entity_description = description
        self._attr_translation_key = description.key
        self._attr_unique_id = entry_entity_unique_id(entry, description.key)
        self._attr_device_info = entry_device_info(entry)

    @property
    def runtime(self) -> Runtime:
        runtime: Runtime = self._entry.runtime_data
        return runtime

    @property
    def wallbox_data(self) -> WallboxData:
        return self.coordinator.current_data()

    @callback
    def _handle_coordinator_update(self) -> None:
        # A vehicle switch replaces observers in place. Publish only its
        # result, never the brief gap between the old and the new car.
        if self.runtime.vehicle_switching:
            return
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        projection = self._projection
        if projection.offline:
            return projection.ready(self.runtime)
        if not self.coordinator.last_update_success:
            return False
        if projection.register is not None and not self.wallbox_data.has(projection.register):
            return False
        if projection.writes and self.runtime.control.read_only:
            return False
        return projection.ready(self.runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._projection.attributes is None:
            return None
        values = self._projection.attributes(self.runtime)
        return {name: value for name, value in values.items() if value is not None} or None
