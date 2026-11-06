"""Stable, optional estimates with no long-term-statistics or polling of their own."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription

from .charge_loss_const import LOSS_SENSOR_KEYS
from .coordinator import MennekesAmtronCoordinator
from .entity import MennekesAmtronEntity
from .entry_types import MennekesAmtronConfigEntry


class AmtronEstimateSensor(MennekesAmtronEntity, SensorEntity):
    """Publish estimates at their declared precision, not meter precision."""

    def __init__(
        self, entry: MennekesAmtronConfigEntry, coordinator: MennekesAmtronCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(entry, coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.key in LOSS_SENSOR_KEYS:
            observer = self.runtime_data.charge_loss
            return observer is not None and observer.available
        cost = self.runtime_data.charging_cost
        return cost is not None and cost.available

    @property
    def native_value(self) -> float | None:
        key = self.entity_description.key
        value: float | None = None
        if key in LOSS_SENSOR_KEYS:
            observer = self.runtime_data.charge_loss
            if observer is not None:
                value = (
                    observer.statistics.last_loss_percent if key == "last_charging_loss"
                    else observer.statistics.average_loss_percent
                )
        else:
            cost = self.runtime_data.charging_cost
            if cost is not None:
                result = cost.current if key == "current_estimated_charging_cost" else cost.last.result
                value = result.cost if result is not None else None
        return round(value, self.entity_description.suggested_display_precision or 0) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        if key in LOSS_SENSOR_KEYS:
            observer = self.runtime_data.charge_loss
            if observer is None:
                return None
            stats = observer.statistics
            attributes: dict[str, Any] = {"sample_count": stats.sample_count}
            if key == "last_charging_loss" and stats.association is not None:
                attributes["association"] = stats.association
            return attributes
        cost = self.runtime_data.charging_cost
        if cost is None:
            return None
        result = cost.current if key == "current_estimated_charging_cost" else cost.last.result
        if result is None:
            return None
        if key == "current_estimated_charging_cost":
            return {"complete": result.complete}
        return {
            "complete": result.complete,
            "energy_kwh": round(result.energy_kwh, 2),
            "unpriced_kwh": round(result.unpriced_kwh, 2),
        }
