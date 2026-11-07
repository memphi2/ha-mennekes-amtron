"""Select platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from enum import IntEnum

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MennekesAmtronCoordinator
from .device.capabilities import capability_is_supported
from .device.decode import RegisterValue
from .device.enums import (
    RequestedPhases,
    SolarChargingMode,
    enum_option,
    enum_options,
)
from .device.registers import REQUESTED_PHASES, SOLAR_CHARGING_MODE
from .entity import MennekesAmtronControlEntity, MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .remote import CONF_SERVER_URL
from .vehicle_profiles import activate_profile, active_profile, profiles

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the selects this device supports."""

    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    coordinator = runtime_data.coordinator
    entities: list[SelectEntity] = []
    if CONF_SERVER_URL in entry.data or not runtime_data.control.read_only:
        entities.append(AmtronSolarModeSelect(entry, coordinator))
        if capability_is_supported(
            identity.layout_version, identity.phase_options_hw, REQUESTED_PHASES.key
        ):
            entities.append(AmtronRequestedPhasesSelect(entry, coordinator))
    if profiles(entry.options):
        entities.append(AmtronVehicleSelect(entry, coordinator))
    async_add_entities(entities)


class AmtronVehicleSelect(MennekesAmtronEntity, SelectEntity):
    """Select one configured vehicle; never start a charge or infer its presence."""

    _attr_translation_key = "active_vehicle"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator) -> None:
        super().__init__(entry, coordinator, "active_vehicle")

    @property
    def available(self) -> bool:
        return True

    def _choices(self) -> dict[str, str | None]:
        choices: dict[str, str | None] = {"none": None}
        for profile in profiles(self._entry.options).values():
            label = profile.name
            # User labels are not stable identifiers. Disambiguate even a label
            # equal to the translated empty-choice token without exposing VINs.
            ordinal = 2
            while label in choices:
                label = f"{profile.name} ({ordinal})"
                ordinal += 1
            choices[label] = profile.id
        return choices

    @property
    def options(self) -> list[str]:
        return list(self._choices())

    @property
    def current_option(self) -> str:
        selected = active_profile(self._entry.options)
        return next(
            (label for label, key in self._choices().items() if selected is not None and key == selected.id),
            "none",
        )

    async def async_select_option(self, option: str) -> None:
        choices = self._choices()
        if option not in choices:
            raise service_validation_error("vehicle_profile_invalid")
        updated = activate_profile(self._entry.options, choices[option])
        self.hass.config_entries.async_update_entry(self._entry, options=updated)


class AmtronSolarModeSelect(MennekesAmtronControlEntity, SelectEntity):
    """The solar charging mode in 0x0D03.

    Writing this register drives the wallbox HMI and its solar LEDs only; the
    energy manager stays responsible for the solar algorithm, and the wallbox
    needs DIP 7 on for the mode to have any effect.
    """

    _attr_translation_key = "solar_charging_mode"
    _attr_entity_category = EntityCategory.CONFIG
    # Home Assistant declares _attr_options as an instance attribute, so
    # annotating it ClassVar does not type-check. The list is never mutated.
    _attr_options = list(enum_options(SolarChargingMode))  # noqa: RUF012

    def __init__(self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            "solar_charging_mode",
            register_key=SOLAR_CHARGING_MODE.key,
        )

    @property
    def current_option(self) -> str | None:
        """Return the solar mode shown by the wallbox."""

        return _option_of(SolarChargingMode, self.register_value)

    async def async_select_option(self, option: str) -> None:
        """Set the solar mode shown by the wallbox."""

        await self.runtime_data.control.async_set_solar_charging_mode(
            SolarChargingMode[option.upper()]
        )


class AmtronRequestedPhasesSelect(MennekesAmtronControlEntity, SelectEntity):
    """The requested phase usage in 0x0D04.

    This entity only exists when the hardware can switch phases. Switching
    phases can abort a charging session and some EVs do not tolerate it, so
    the control layer pauses the charge first and applies the configured
    vehicle-protection interval when a vehicle is connected.
    """

    _attr_translation_key = "requested_phases"
    _attr_options = list(enum_options(RequestedPhases))  # noqa: RUF012

    def __init__(self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator) -> None:
        super().__init__(
            entry,
            coordinator,
            "requested_phases",
            register_key=REQUESTED_PHASES.key,
        )

    @property
    def current_option(self) -> str | None:
        """Return the requested phase usage."""

        return _option_of(RequestedPhases, self.register_value)

    async def async_select_option(self, option: str) -> None:
        """Request single-phase or all-phase charging."""

        await self.runtime_data.control.async_set_requested_phases(
            RequestedPhases[option.upper()]
        )


def _option_of(enum_class: type[IntEnum], value: RegisterValue) -> str | None:
    if not isinstance(value, int):
        return None
    try:
        return enum_option(enum_class(value))
    except ValueError:
        return None
