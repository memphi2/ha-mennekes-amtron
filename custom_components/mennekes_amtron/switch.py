"""Switch platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .registers import CHARGING_CURRENT_EMS, CHARGING_RELEASE, LOCK_EVSE

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the command switches this device supports."""

    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            AmtronPauseSwitch(entry, coordinator),
            AmtronChargingReleaseSwitch(entry, coordinator),
            AmtronLockSwitch(entry, coordinator),
        ]
    )


class AmtronPauseSwitch(MennekesAmtronEntity, SwitchEntity):
    """Pause and resume charging the documented way.

    Turning this on writes the vendor's pause value to 0x0302 so the wallbox
    signals 0 A to the EV. Unlike the charging release it does not switch the
    relay, which is why it is the recommended way to pause.
    """

    _attr_translation_key = "charging_paused"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            "charging_paused",
            register_key=CHARGING_CURRENT_EMS.key,
        )

    @property
    def is_on(self) -> bool:
        """Return true while the EMS limit holds the pause value."""

        return self.runtime_data.control.is_paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Pause charging."""

        await self.runtime_data.control.async_pause_charging()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Resume charging with the stored setpoint."""

        await self.runtime_data.control.async_resume_charging()


class AmtronChargingReleaseSwitch(MennekesAmtronEntity, SwitchEntity):
    """The energy-manager charging release in 0x0D05.

    This opens and closes the wallbox relay. For everyday pausing use the
    charging-pause switch instead; switching the relay wears it out.
    """

    _attr_translation_key = "charging_release"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            "charging_release",
            register_key=CHARGING_RELEASE.key,
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the wallbox relay is released for charging."""

        value = self.register_value
        return value == 1 if isinstance(value, int) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Release charging."""

        await self.runtime_data.control.async_set_charging_release(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Block charging."""

        await self.runtime_data.control.async_set_charging_release(False)


class AmtronLockSwitch(MennekesAmtronEntity, SwitchEntity):
    """Lock the wallbox against charging, through 0x0D06."""

    _attr_translation_key = "lock_evse"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: Any
    ) -> None:
        super().__init__(
            entry, coordinator, "lock_evse", register_key=LOCK_EVSE.key
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the wallbox is locked."""

        value = self.register_value
        return value == 1 if isinstance(value, int) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the wallbox."""

        await self.runtime_data.control.async_set_lock_evse(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the wallbox."""

        await self.runtime_data.control.async_set_lock_evse(False)
