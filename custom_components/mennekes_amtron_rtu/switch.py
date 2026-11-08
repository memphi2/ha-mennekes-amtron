"""Switch platform for the MENNEKES AMTRON integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .coordinator import MennekesAmtronCoordinator
from .device.errors import DeviceError
from .device.registers import (
    CHARGING_CURRENT_EMS,
    CHARGING_RELEASE,
    CP_STATE,
    EVSE_STATE,
    LOCK_EVSE,
)
from .device.vehicle import vehicle_connected
from .entity import MennekesAmtronControlEntity
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error

if TYPE_CHECKING:
    from homeassistant.components.switch.const import SwitchDeviceClass
else:
    from homeassistant.components.switch import SwitchDeviceClass

PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the command switches this device supports."""

    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = [
        AmtronPauseSwitch(entry, coordinator),
        AmtronChargingReleaseSwitch(entry, coordinator),
        AmtronLockSwitch(entry, coordinator),
        AmtronReleaseOffOnUnplugSwitch(entry, coordinator),
    ]
    if entry.runtime_data.plug_charge is not None:
        entities.append(AmtronPlugChargeSwitch(entry, coordinator))
    async_add_entities(entities)


class AmtronPlugChargeSwitch(MennekesAmtronControlEntity, SwitchEntity):
    """Configure automatic charging permission directly on the HA device."""

    _attr_translation_key = "virtual_plug_charge"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator) -> None:
        super().__init__(entry, coordinator, "virtual_plug_charge")

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.runtime_data.control.supports_plug_charge
            and (not self.runtime_data.control.read_only or self.is_on)
        )

    @property
    def is_on(self) -> bool:
        return self.runtime_data.control.plug_charge.get("enabled") is True

    async def _async_set_enabled(self, enabled: bool) -> None:
        observer = self.runtime_data.plug_charge
        if observer is None:
            raise service_validation_error("plug_charge_unavailable")
        await observer.async_set_enabled(enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)


class AmtronReleaseOffOnUnplugSwitch(MennekesAmtronControlEntity, SwitchEntity, RestoreEntity):
    """Switch the charging release off whenever a vehicle is unplugged.

    Without Virtual Plug & Charge, an open release lets any car charge. This
    HA-side rule closes it after a confirmed disconnect, so the next car needs
    an explicit release. With the gate enabled, the gate already owns 0x0D05.
    """

    _attr_translation_key = "release_off_on_unplug"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator) -> None:
        super().__init__(entry, coordinator, "release_off_on_unplug")
        self._attr_is_on = False
        self._connected: bool | None = None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and (
            not self.runtime_data.control.read_only or self.is_on is True
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) is not None:
            self._attr_is_on = state.state == STATE_ON
        self._connected = self._vehicle_connected()

    def _vehicle_connected(self) -> bool | None:
        data = self.wallbox_data
        return vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=self.runtime_data.identity.layout_version >= CP_STATE.min_layout,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        connected = self._vehicle_connected()
        # Unknown readings never count as a disconnect.
        unplugged = connected is False and self._connected is True
        if connected is not None:
            self._connected = connected
        control = self.runtime_data.control
        if (
            unplugged and self.is_on and not control.read_only
            and control.plug_charge.get("enabled") is not True
            and self.wallbox_data.get(CHARGING_RELEASE.key) != 0
        ):
            self.hass.async_create_task(self._release_off())
        super()._handle_coordinator_update()

    async def _release_off(self) -> None:
        try:
            await self.runtime_data.control.async_set_charging_release(False)
        except (HomeAssistantError, DeviceError, OSError) as err:
            _LOGGER.warning("Cannot switch off the charging release after unplugging (%s)", err)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


type IntentKey = Literal["pause_requested", "charging_release_requested"]


@dataclass(frozen=True)
class ControlIntentRestoreData(ExtraStoredData):
    """Persist explicit intent outside Recorder and register readback."""

    key: IntentKey
    requested: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {self.key: self.requested}


class AmtronIntentSwitch(MennekesAmtronControlEntity, SwitchEntity, RestoreEntity):
    """Restore saved intent without replacing live service or runtime intent."""

    _intent_key: IntentKey
    _legacy_stop_state: str
    _legacy_stop_intent: bool

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return ControlIntentRestoreData(
            self._intent_key, getattr(self.runtime_data.control, self._intent_key)
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        control = self.runtime_data.control
        if getattr(control, self._intent_key) is not None:
            return
        if extra := await self.async_get_last_extra_data():
            values = extra.as_dict()
            requested = values.get(self._intent_key)
            if self._intent_key in values and (
                requested is None or isinstance(requested, bool)
            ):
                setattr(control, self._intent_key, requested)
                return
        # Legacy readback is evidence of a stop only, never permission to start.
        if (
            (state := await self.async_get_last_state()) is not None
            and state.state == self._legacy_stop_state
        ):
            setattr(control, self._intent_key, self._legacy_stop_intent)


class AmtronPauseSwitch(AmtronIntentSwitch):
    """Pause and resume charging the documented way.

    Turning this on writes the vendor's pause value to 0x0302 so the wallbox
    signals 0 A to the EV. Unlike the charging release it does not switch the
    relay, which is why it is the recommended way to pause.
    """

    _attr_translation_key = "charging_paused"
    _intent_key: IntentKey = "pause_requested"
    _legacy_stop_state = "on"
    _legacy_stop_intent = True

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
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

class AmtronChargingReleaseSwitch(AmtronIntentSwitch):
    """The energy-manager charging release in 0x0D05.

    This opens and closes the wallbox relay. For everyday pausing use the
    charging-pause switch instead; switching the relay wears it out.
    """

    _attr_translation_key = "charging_release"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _intent_key: IntentKey = "charging_release_requested"
    _legacy_stop_state = "off"
    _legacy_stop_intent = False

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
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

class AmtronLockSwitch(MennekesAmtronControlEntity, SwitchEntity):
    """Lock the wallbox against charging, through 0x0D06."""

    _attr_translation_key = "lock_evse"

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator
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
