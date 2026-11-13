"""What the departure plan learns about a car stays bounded and plausible."""

from __future__ import annotations

import pytest

from custom_components.mennekes_amtron_rtu.device.departure_model import (
    MAX_CARS,
    CarModel,
    DepartureModel,
    vehicle_key,
)


def test_efficiency_blends_plausible_charges_only():
    car = CarModel().with_charge(level_gain=10, capacity_kwh=60, metered_kwh=7.5)
    assert car.efficiency == 0.8
    # A later charge moves the value by a part of the difference.
    assert car.with_charge(level_gain=10, capacity_kwh=60, metered_kwh=6.0).efficiency == pytest.approx(0.86)
    # Too small, or not one charge: nothing changes.
    for gain, metered in ((4, 3.0), (10, 0.5), (10, 20.0), (10, 5.0)):
        assert car.with_charge(level_gain=gain, capacity_kwh=60, metered_kwh=metered) is car
    # A sample slightly above one counts as a lossless charge, not more.
    assert CarModel().with_charge(level_gain=10, capacity_kwh=60, metered_kwh=5.8).efficiency == 1.0
    assert CarModel().with_charge(level_gain=10, capacity_kwh=60, metered_kwh=11.0).efficiency == 0.6


def test_power_is_kept_per_number_of_phases():
    car = CarModel().with_power(3, 10.5).with_power(1, 3.6)
    assert car.full_kw == (3.6, None, 10.5)
    assert car.with_power(3, 9.5).power(3) == pytest.approx(10.2)
    assert (car.power(2), car.power(0)) == (None, None)
    for phases, kw in ((0, 5.0), (3, 0.5), (3, 60.0)):
        assert car.with_power(phases, kw) is car


def test_a_car_model_round_trips_and_rejects_damage():
    car = CarModel(0.85, (None, 7.2, 10.5))
    assert CarModel.restore(car.snapshot()) == car
    assert CarModel.restore(CarModel().snapshot()) == CarModel()
    for raw in (
        None, {}, {"efficiency": 0.9, "full_kw": [None, None]}, {"efficiency": 0.9, "full_kw": "x"},
        {"efficiency": 1.5, "full_kw": [None, None, None]}, {"efficiency": True, "full_kw": [None, None, None]},
        {"efficiency": None, "full_kw": [None, None, 500]}, {"efficiency": None, "full_kw": [None, "7", None]},
        {"efficiency": float("nan"), "full_kw": [None, None, None]},
    ):
        with pytest.raises(ValueError, match="learned"):
            CarModel.restore(raw)


def test_the_cars_are_bounded_and_keyed_by_profile():
    model = DepartureModel()
    assert model.efficiency(None) == 0.9
    assert model.efficiency("profile:bmw:sensor.soc|") == 0.9
    assert model.learn("profile:bmw:sensor.soc|", CarModel(0.8)) is True
    assert model.learn("profile:bmw:vehicle:abc|", CarModel(0.8)) is False
    assert model.efficiency("profile:bmw:another|") == 0.8
    assert vehicle_key("sensor.soc|") == "sensor.soc|"
    for index in range(MAX_CARS):
        model.learn(f"profile:car{index}:x", CarModel(0.7))
    assert len(model.cars) == MAX_CARS
    assert "profile:bmw" not in model.cars
    assert DepartureModel.restore(model.snapshot()).cars == model.cars
    for raw in (None, [], {"": CarModel().snapshot()}, {5: CarModel().snapshot()}, {"x" * 513: CarModel().snapshot()},
                {f"car{index}": CarModel().snapshot() for index in range(MAX_CARS + 1)}):
        with pytest.raises(ValueError, match="learned"):
            DepartureModel.restore(raw)
