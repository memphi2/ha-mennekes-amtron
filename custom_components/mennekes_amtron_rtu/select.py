"""Selects: the phases to charge with, and the vehicle that is plugged in."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.decode import enum_option
from .device.enums import RequestedPhases, enum_options
from .device.registers import REQUESTED_PHASES, SWITCHED_PHASES
from .entity import AmtronEntity, Projection, Runtime, wanted
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .vehicle_detection import automatic_selection_available
from .vehicle_profiles import (
    CONF_VEHICLE_AUTO_SELECTION,
    activate_profile,
    active_profile,
    profiles,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AmtronSelectDescription(Projection, SelectEntityDescription):
    current: Callable[[Runtime], str | None]
    select: Callable[[Runtime, str], Awaitable[None]]


SELECTS: tuple[AmtronSelectDescription, ...] = (
    # Exists only when the hardware can switch phases. The service pauses the
    # charge first and waits the vehicle-protection interval.
    AmtronSelectDescription(
        key="requested_phases", register=REQUESTED_PHASES.key, writes=True,
        options=list(enum_options(RequestedPhases)),
        exists=lambda runtime: runtime.controls,
        current=lambda runtime: enum_option(
            REQUESTED_PHASES, runtime.coordinator.current_data().get(REQUESTED_PHASES.key)
        ),
        select=lambda runtime, option: runtime.control.async_set_requested_phases(
            RequestedPhases[option.upper()]
        ),
        # The phases the wallbox will really use when it next closes the relay.
        attributes=lambda runtime: {
            "switched": enum_option(
                SWITCHED_PHASES, runtime.coordinator.current_data().get(SWITCHED_PHASES.key)
            ),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[SelectEntity] = [
        AmtronSelect(entry, description) for description in wanted(hass, entry, SELECTS)
    ]
    if profiles(entry.options):
        entities.append(AmtronVehicleSelect(entry, _VEHICLE))
    async_add_entities(entities)


class AmtronSelect(AmtronEntity, SelectEntity):
    entity_description: AmtronSelectDescription

    @property
    def current_option(self) -> str | None:
        return self.entity_description.current(self.runtime)

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.select(self.runtime, option)


@dataclass(frozen=True, kw_only=True)
class _VehicleDescription(Projection, SelectEntityDescription):
    pass


_VEHICLE = _VehicleDescription(key="active_vehicle", entity_category=EntityCategory.CONFIG)


class AmtronVehicleSelect(AmtronEntity, SelectEntity):
    """Choose one configured vehicle. A setting of this entry, not of the wallbox:
    it never starts a charge and works while the wallbox is unreachable.
    """

    @property
    def available(self) -> bool:
        return True

    def _choices(self) -> dict[str, str | None]:
        options = self._entry.options
        choices: dict[str, str | None] = {"none": None}
        if automatic_selection_available(options) or options.get(CONF_VEHICLE_AUTO_SELECTION) is True:
            choices["automatic"] = "automatic"
        for profile in profiles(options).values():
            label = profile.name
            # Names are not identifiers: two cars, or a car called like a
            # fixed choice, still get distinct labels.
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
        if self._entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True:
            return "automatic"
        selected = active_profile(self._entry.options)
        return next(
            (label for label, key in self._choices().items() if selected is not None and key == selected.id),
            "none",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        profile = active_profile(self._entry.options)
        return {"active_vehicle": profile.name if profile is not None else None}

    async def async_select_option(self, option: str) -> None:
        choices = self._choices()
        if option not in choices:
            raise service_validation_error("vehicle_profile_invalid")
        if choices[option] == "automatic":
            updated = {**self._entry.options, CONF_VEHICLE_AUTO_SELECTION: True}
        else:
            updated = activate_profile(self._entry.options, choices[option])
            updated[CONF_VEHICLE_AUTO_SELECTION] = False
        self.hass.config_entries.async_update_entry(self._entry, options=updated)
