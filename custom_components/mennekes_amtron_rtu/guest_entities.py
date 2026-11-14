"""One guest-start button; all limits are ordinary shared charging controls."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error


class GuestButton(MennekesAmtronEntity, ButtonEntity):
    _attr_translation_key = "guest_start"

    def __init__(self, entry: MennekesAmtronConfigEntry) -> None:
        super().__init__(entry, entry.runtime_data.coordinator, "guest_start")

    @property
    def available(self) -> bool:
        orders = self.runtime_data.orders
        return self.coordinator.last_update_success and not self.runtime_data.control.read_only and orders is not None and orders.jobs is not None

    async def async_press(self) -> None:
        orders = self.runtime_data.orders
        if orders is None:
            raise service_validation_error("service_order_failed")
        await orders.async_start_guest()
