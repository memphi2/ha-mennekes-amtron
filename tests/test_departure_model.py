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
    # The first sample moves the assumed 0.9 by a part, like every later one.
    car = CarModel().with_charge(level_gain=10, capacity_kwh=60, metered_kwh=7.5)
    assert car.efficiency == 0.87
    assert car.with_charge(level_gain=10, capacity_kwh=60, metered_kwh=6.0).efficiency == pytest.approx(0.909)
    # Too small, or not one charge: nothing changes.
    for gain, metered in ((9, 6.0), (10, 0.5), (10, 20.0), (10, 5.0)):
        assert car.with_charge(level_gain=gain, capacity_kwh=60, metered_kwh=metered) is car
    # A sample slightly above one counts as a lossless charge, not more.
    assert CarModel().with_charge(level_gain=10, capacity_kwh=60, metered_kwh=5.8).efficiency == 0.93
    assert CarModel().with_charge(level_gain=10, capacity_kwh=60, metered_kwh=11.0).efficiency == 0.81


def test_power_is_kept_per_offered_ampere_and_number_of_phases():
    car = CarModel().with_power(3, 10.5, 16).with_power(1, 3.6, 16)
    assert car.kw_per_a == (0.225, None, 0.6562)
    assert car.with_power(3, 9.5, 16).power(3, 16) == pytest.approx(10.2, abs=0.01)
    # A lower maximum current expects proportionally less.
    assert car.power(3, 8) == pytest.approx(5.25, abs=0.01)
    assert (car.power(2, 16), car.power(0, 16), car.power(3, 0)) == (None, None, None)
    # A first sample above the nominal power is not trusted beyond it.
    assert CarModel().with_power(3, 12.0, 16).kw_per_a[2] == 0.69
    for phases, kw, current in ((0, 5.0, 16), (3, 0.5, 16), (3, 60.0, 16), (3, 7.0, 0), (3, 40.0, 32)):
        assert car.with_power(phases, kw, current) is car


def test_a_car_model_round_trips_and_rejects_damage():
    car = CarModel(0.85, (None, 0.45, 0.65))
    assert CarModel.restore(car.snapshot()) == car
    assert CarModel.restore(CarModel().snapshot()) == CarModel()
    for raw in (
        None, {}, {"efficiency": 0.9, "kw_per_a": [None, None]}, {"efficiency": 0.9, "kw_per_a": "x"},
        {"efficiency": 0.9, "full_kw": [None, None, None]},
        {"efficiency": 1.5, "kw_per_a": [None, None, None]}, {"efficiency": True, "kw_per_a": [None, None, None]},
        {"efficiency": None, "kw_per_a": [None, None, 5]}, {"efficiency": None, "kw_per_a": [None, "7", None]},
        {"efficiency": float("nan"), "kw_per_a": [None, None, None]},
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
