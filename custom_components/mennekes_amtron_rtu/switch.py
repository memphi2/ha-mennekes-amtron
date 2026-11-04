"""Switch platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .device.registers import CHARGING_CURRENT_EMS, CHARGING_RELEASE, LOCK_EVSE
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.switch.const import SwitchDeviceClass
else:
    from homeassistant.components.switch import SwitchDeviceClass

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


@dataclass(frozen=True)
class PauseRestoreData(ExtraStoredData):
    """Persist pause intent outside Recorder and register readback."""

    requested: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {"pause_requested": self.requested}


class AmtronPauseSwitch(MennekesAmtronEntity, SwitchEntity, RestoreEntity):
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
    def is_on(self) -> bool | None:
        """Return true while the EMS limit holds the pause value."""

        return self.runtime_data.control.is_paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Pause charging."""

        await self.runtime_data.control.async_pause_charging()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Resume charging with the stored setpoint."""

        await self.runtime_data.control.async_resume_charging()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return PauseRestoreData(self.runtime_data.control.pause_requested)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        control = self.runtime_data.control
        if control.pause_requested is not None:
            return
        if extra := await self.async_get_last_extra_data():
            values = extra.as_dict()
            requested = values.get("pause_requested")
            if "pause_requested" in values and (
                requested is None or isinstance(requested, bool)
            ):
                control.pause_requested = requested
                return
        if (state := await self.async_get_last_state()) is not None and state.state == "on":
            control.pause_requested = True


@dataclass(frozen=True)
class ChargingReleaseRestoreData(ExtraStoredData):
    """Persist intent without adding a changing entity attribute to Recorder."""

    requested: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {"charging_release_requested": self.requested}


class AmtronChargingReleaseSwitch(MennekesAmtronEntity, SwitchEntity, RestoreEntity):
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

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Keep explicit intent separate from relay readback during a fault."""

        return ChargingReleaseRestoreData(
            self.runtime_data.control.charging_release_requested
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        control = self.runtime_data.control
        if control.charging_release_requested is not None:
            return
        if extra := await self.async_get_last_extra_data():
            values = extra.as_dict()
            requested = values.get("charging_release_requested")
            if "charging_release_requested" in values and (
                requested is None or isinstance(requested, bool)
            ):
                control.charging_release_requested = requested
                return
        # Older versions did not store intent separately. Preserve an old off
        # state conservatively rather than enabling charging during an upgrade.
        if (state := await self.async_get_last_state()) is not None and state.state == "off":
            control.charging_release_requested = False


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
