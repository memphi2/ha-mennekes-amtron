"""Native HA controls for the app-owned draft; no HA restore state or helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.datetime import DateTimeEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy

from .device.job_draft import DRAFT_STATES
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry
from .exceptions import service_validation_error
from .service_orders import ServiceOrders


class JobEntity(MennekesAmtronEntity):
    """Read from the same service snapshot as the app's own editor."""

    def __init__(self, entry: MennekesAmtronConfigEntry, key: str) -> None:
        super().__init__(entry, entry.runtime_data.coordinator, key)
        self._attr_translation_key = key

    @property
    def orders(self) -> ServiceOrders:
        orders = self.runtime_data.orders
        if orders is None:
            raise service_validation_error("service_order_failed")
        return orders

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and not self.runtime_data.control.read_only
            and self.orders.draft is not None
        )

    def draft_value(self, field: str) -> Any:
        draft = self.orders.draft
        values = (
            draft.get("values")
            if draft is not None and not draft.get("storage_error")
            else None
        )
        return values.get(field) if isinstance(values, dict) else None


class JobNumber(JobEntity, NumberEntity):
    """Draft current, energy and optional immediate reserve."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 0.1

    def __init__(self, entry: MennekesAmtronConfigEntry, field: str) -> None:
        super().__init__(entry, f"job_{field}")
        self.field = field
        self._attr_native_unit_of_measurement = (
            UnitOfElectricCurrent.AMPERE
            if field == "current_a"
            else UnitOfEnergy.KILO_WATT_HOUR
        )
        self._attr_native_min_value = (
            6 if field == "current_a" else 0 if field == "reserve_kwh" else 0.1
        )
        self._attr_entity_registry_enabled_default = field != "reserve_kwh"

    @property
    def native_max_value(self) -> float:
        status = self.orders.jobs
        if self.field != "current_a":
            return 1000.0
        value = status.get("max_current_a") if status is not None else None
        return (
            float(value)
            if isinstance(value, (float, int))
            and not isinstance(value, bool)
            and 6 <= value <= 32
            else 6.0
        )

    @property
    def native_value(self) -> float | None:
        value = self.draft_value(self.field)
        return (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )

    async def async_set_native_value(self, value: float) -> None:
        await self.orders.async_edit_draft(self.field, value)


class JobDateTime(JobEntity, DateTimeEntity):
    """Optional earliest start and required finish, persisted by the app."""

    def __init__(self, entry: MennekesAmtronConfigEntry, field: str) -> None:
        super().__init__(entry, f"job_{field}")
        self.field = field
        self._attr_entity_registry_enabled_default = field != "start_at"

    @property
    def native_value(self) -> datetime | None:
        value = self.draft_value(self.field)
        return datetime.fromisoformat(value) if isinstance(value, str) else None

    async def async_set_value(self, value: datetime) -> None:
        await self.orders.async_edit_draft(self.field, value.isoformat())


class JobButton(JobEntity, ButtonEntity):
    """Use the same commands as the app; no anonymous service target."""

    def __init__(self, entry: MennekesAmtronConfigEntry, action: str) -> None:
        super().__init__(entry, f"job_{action}")
        self.action = action

    async def async_press(self) -> None:
        if self.action == "start":
            await self.orders.async_start_draft()
        elif self.action == "stop":
            await self.orders.async_stop_job()
        elif self.action == "clear":
            await self.orders.async_clear_job()
        else:
            await self.orders.async_reset_draft()


class JobDraftStatus(JobEntity, SensorEntity):
    """Stable readiness reason, never a ticking countdown or revision attribute."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, entry: MennekesAmtronConfigEntry) -> None:
        super().__init__(entry, "job_draft_status")
        self._attr_options = list(DRAFT_STATES)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.orders.draft is not None

    @property
    def native_value(self) -> str | None:
        draft = self.orders.draft
        status = draft.get("status") if draft is not None else None
        return status if isinstance(status, str) and status in DRAFT_STATES else None
