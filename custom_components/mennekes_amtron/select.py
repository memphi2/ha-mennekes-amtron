"""Select platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .capabilities import capability_is_supported
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .enums import (
    RequestedPhases,
    SolarChargingMode,
    enum_option,
    enum_options,
)
from .registers import REQUESTED_PHASES, SOLAR_CHARGING_MODE

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
    entities: list[SelectEntity] = [AmtronSolarModeSelect(entry, coordinator)]
    if capability_is_supported(
        identity.layout_version, identity.phase_options_hw, REQUESTED_PHASES.key
    ):
        entities.append(AmtronRequestedPhasesSelect(entry, coordinator))
    async_add_entities(entities)


class AmtronSolarModeSelect(MennekesAmtronEntity, SelectEntity):
    """The solar charging mode in 0x0D03.

    Writing this register drives the wallbox HMI and its solar LEDs only; the
    energy manager stays responsible for the solar algorithm, and the wallbox
    needs DIP 7 on for the mode to have any effect.
    """

    _attr_translation_key = "solar_charging_mode"
    _attr_options: ClassVar[list[str]] = list(enum_options(SolarChargingMode))

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            "solar_charging_mode",
            register_key=SOLAR_CHARGING_MODE.key,
        )

    @property
    def current_option(self) -> str | None:
        """Return the active solar charging mode."""

        return _option_of(SolarChargingMode, self.register_value)

    async def async_select_option(self, option: str) -> None:
        """Set the solar charging mode."""

        await self.runtime_data.control.async_set_solar_charging_mode(
            SolarChargingMode[option.upper()]
        )


class AmtronRequestedPhasesSelect(MennekesAmtronEntity, SelectEntity):
    """The requested phase usage in 0x0D04.

    This entity only exists when the hardware can switch phases. Switching
    phases can abort a charging session and some EVs do not tolerate it, so
    the control layer pauses the charge first and the vendor's own interval
    recommendation is enforced.
    """

    _attr_translation_key = "requested_phases"
    _attr_options: ClassVar[list[str]] = list(enum_options(RequestedPhases))

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
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


def _option_of(enum_class: Any, value: Any) -> str | None:
    if not isinstance(value, int):
        return None
    try:
        return enum_option(enum_class(value))
    except ValueError:
        return None
