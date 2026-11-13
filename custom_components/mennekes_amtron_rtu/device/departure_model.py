"""What a car's own charges taught the departure plan: bounded and advisory."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from .charging_plan import DEFAULT_BATTERY_EFFICIENCY

MAX_CARS = 8
MAX_VEHICLE_LENGTH = 512
# Share of the metered energy that reaches the battery. A learned value also
# absorbs a usable capacity that is slightly off.
EFFICIENCY_LOW, EFFICIENCY_HIGH = 0.6, 1.0
# Outside this the two readings do not describe one charge, for example
# after the car heated its cabin from the wallbox.
_SAMPLE_LOW, _SAMPLE_HIGH = 0.5, 1.1
# The car reports whole percent: a smaller rise is mostly rounding.
MIN_LEVEL_GAIN = 5.0
MIN_ENERGY_KWH = 1.0
# Metered power at full current is averaged over at least this long.
POWER_WINDOW_SECONDS = 600.0
MIN_POWER_KW, MAX_POWER_KW = 1.0, 50.0
_WEIGHT = 0.3


def vehicle_key(source: str) -> str:
    """A vehicle profile stays the same car when its source description changes."""
    parts = source.split(":", 2)
    return ":".join(parts[:2]) if len(parts) == 3 and parts[0] == "profile" else source


def _number(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def _blend(known: float | None, sample: float) -> float:
    return sample if known is None else known + (sample - known) * _WEIGHT


@dataclass(frozen=True, slots=True)
class CarModel:
    """One car's learned efficiency and its metered power at full current, by phases."""

    efficiency: float | None = None
    full_kw: tuple[float | None, float | None, float | None] = (None, None, None)

    @classmethod
    def restore(cls, raw: object) -> CarModel:
        if not isinstance(raw, dict) or raw.keys() != {"efficiency", "full_kw"}:
            raise ValueError("invalid learned car fields")
        powers = raw["full_kw"]
        if not isinstance(powers, list) or len(powers) != 3:
            raise ValueError("invalid learned power")
        efficiency = _number(raw["efficiency"], EFFICIENCY_LOW, EFFICIENCY_HIGH)
        full = tuple(_number(value, MIN_POWER_KW, MAX_POWER_KW) for value in powers)
        if (raw["efficiency"] is not None and efficiency is None) or any(
            value is not None and known is None for value, known in zip(powers, full, strict=True)
        ):
            raise ValueError("learned car value is out of range")
        return cls(efficiency, (full[0], full[1], full[2]))

    def snapshot(self) -> dict[str, Any]:
        return {"efficiency": self.efficiency, "full_kw": list(self.full_kw)}

    def with_charge(self, *, level_gain: float, capacity_kwh: float, metered_kwh: float) -> CarModel:
        """Learn from two reports of the car and the energy metered between them."""
        if level_gain < MIN_LEVEL_GAIN or metered_kwh < MIN_ENERGY_KWH:
            return self
        sample = level_gain * capacity_kwh / 100 / metered_kwh
        if not _SAMPLE_LOW <= sample <= _SAMPLE_HIGH:
            return self
        learned = _blend(self.efficiency, min(EFFICIENCY_HIGH, max(EFFICIENCY_LOW, sample)))
        return replace(self, efficiency=round(learned, 4))

    def with_power(self, phases: int, kw: float) -> CarModel:
        """Learn the power the car really drew while the plan offered full current."""
        if phases not in (1, 2, 3) or not MIN_POWER_KW <= kw <= MAX_POWER_KW:
            return self
        powers = list(self.full_kw)
        powers[phases - 1] = round(_blend(powers[phases - 1], kw), 3)
        return replace(self, full_kw=(powers[0], powers[1], powers[2]))

    def power(self, phases: int) -> float | None:
        return self.full_kw[phases - 1] if phases in (1, 2, 3) else None


@dataclass(slots=True)
class DepartureModel:
    """The cars the plan has learned from; the longest unused one makes room."""

    cars: dict[str, CarModel] = field(default_factory=dict)

    @classmethod
    def restore(cls, raw: object) -> DepartureModel:
        if not isinstance(raw, dict) or len(raw) > MAX_CARS:
            raise ValueError("invalid learned cars")
        cars: dict[str, CarModel] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not 1 <= len(key) <= MAX_VEHICLE_LENGTH:
                raise ValueError("invalid learned car name")
            cars[key] = CarModel.restore(value)
        return cls(cars)

    def snapshot(self) -> dict[str, Any]:
        return {key: car.snapshot() for key, car in self.cars.items()}

    def car(self, vehicle: str | None) -> CarModel:
        return self.cars.get(vehicle_key(vehicle), CarModel()) if vehicle else CarModel()

    def efficiency(self, vehicle: str | None) -> float:
        return self.car(vehicle).efficiency or DEFAULT_BATTERY_EFFICIENCY

    def learn(self, vehicle: str, car: CarModel) -> bool:
        """Keep a changed model; True when something was learned."""
        key = vehicle_key(vehicle)
        if self.cars.get(key, CarModel()) == car:
            return False
        self.cars.pop(key, None)
        self.cars[key] = car
        while len(self.cars) > MAX_CARS:
            del self.cars[next(iter(self.cars))]
        return True
