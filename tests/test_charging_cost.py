"""Charging costs preserve uncertainty and never apply discounts twice."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.mennekes_amtron_rtu.device import charging_cost
from custom_components.mennekes_amtron_rtu.device.charging_cost import (
    MAX_PRICE_CHANGES,
    MAX_TARIFF_INTERVAL,
    CostAccumulator,
    CostResult,
    TariffConfig,
    TariffWindow,
)


def at(value):
    return datetime.fromisoformat(value).timestamp()


def window(start="22:00", end="06:00", rate="nt"):
    return {"start": start, "end": end, "rate": rate}


def config(**overrides):
    raw = {
        "module": "module_3", "regular_network": 0.1,
        "price_includes_network": False, "high_network": 0.2,
        "low_network": 0.02, "windows": [window(), window("17:00", "20:00", "ht")],
        "active_quarters": [1, 2, 3, 4], "tariff_year": 2026,
        "timezone": "Europe/Berlin",
    }
    raw.update(overrides)
    return raw


@pytest.mark.parametrize("price", [-0.2, 0, 0.35, 2])
def test_plain_tariff_preserves_complete_end_price_without_any_adjustment(price):
    tariff = TariffConfig.parse({})
    assert tariff.price_at(10, price) == price
    assert tariff.average_price(10, 1000, price) == price
    assert TariffConfig.parse(tariff.as_dict()) == tariff


@pytest.mark.parametrize("price", [None, True, "0.3", float("nan"), float("inf"), 10**1000])
def test_unknown_price_never_turns_into_zero_or_a_network_only_price(price):
    for tariff in (TariffConfig(), TariffConfig.parse(config())):
        assert tariff.price_at(10, price) is None
        assert tariff.average_price(10, 1000, price) is None


@pytest.mark.parametrize(("includes", "base", "expected"), [
    (True, 0.3, 0.24), (False, 0.2, 0.24), (True, -0.3, -0.36),
])
def test_module_two_replaces_only_the_regular_network_component(includes, base, expected):
    tariff = TariffConfig.parse({
        "module": "module_2", "regular_network": 0.1,
        "price_includes_network": includes,
    })
    assert tariff.price_at(10, base) == pytest.approx(expected)
    assert TariffConfig.parse(tariff.as_dict()) == tariff


@pytest.mark.parametrize(("moment", "expected"), [
    ("2026-01-01T20:59:59+00:00", 0.3),
    ("2026-01-01T21:00:00+00:00", 0.22),
    ("2026-01-02T04:59:59+00:00", 0.22),
    ("2026-01-02T05:00:00+00:00", 0.3),
    ("2026-01-02T16:00:00+00:00", 0.4),
    ("2026-01-02T19:00:00+00:00", 0.3),
    ("2026-04-02T15:00:00+00:00", 0.3),
    ("2026-10-02T15:00:00+00:00", 0.4),
    ("2026-12-31T23:00:00+00:00", None),
])
def test_module_three_published_windows_quarters_and_local_year(moment, expected):
    tariff = TariffConfig.parse(config(active_quarters=[1, 4]))
    result = tariff.price_at(at(moment), 0.2)
    assert result is None if expected is None else result == pytest.approx(expected)
    assert TariffConfig.parse(tariff.as_dict()) == tariff


def test_module_three_replaces_existing_regular_fee_and_allows_rounded_published_rates():
    tariff = TariffConfig.parse(config(
        price_includes_network=True, low_network=0.04001, high_network=0.20001,
    ))
    assert tariff.price_at(at("2026-01-01T22:00:00+00:00"), 0.3) == pytest.approx(0.24001)
    assert tariff.price_at(at("2026-01-01T17:00:00+00:00"), 0.3) == pytest.approx(0.40001)


@pytest.mark.parametrize(("start", "end", "nt_start", "nt_end", "expected"), [
    ("2026-03-29T00:00:00+00:00", "2026-03-29T02:00:00+00:00", "01:30", "03:30", 0.06),
    ("2026-03-29T00:00:00+00:00", "2026-03-29T02:00:00+00:00", "02:15", "02:45", 0.1),
    ("2026-10-25T00:00:00+00:00", "2026-10-25T03:00:00+00:00", "02:15", "02:45", 0.22 / 3),
    ("2026-01-01T20:59:30+00:00", "2026-01-01T21:00:30+00:00", "22:00", "06:00", 0.06),
])
def test_schedule_uses_elapsed_utc_time_across_both_dst_transitions(start, end, nt_start, nt_end, expected):
    tariff = TariffConfig.parse(config(windows=[window(nt_start, nt_end), window("17:00", "20:00", "ht")]))
    assert tariff.average_price(at(start), at(end), 0) == pytest.approx(expected)


def test_quarter_switch_and_year_expiry_split_a_meter_interval():
    tariff = TariffConfig.parse(config(active_quarters=[1, 4]))
    start, end = at("2026-03-31T21:00:00+00:00"), at("2026-03-31T23:00:00+00:00")
    assert tariff.average_price(start, end, 0) == pytest.approx(0.06)
    start, end = at("2026-12-31T22:30:00+00:00"), at("2026-12-31T23:30:00+00:00")
    assert tariff.average_price(start, end, 0) is None
    accumulator = CostAccumulator(tariff, max_gap=7200)
    accumulator.sample(start, 100, 0)
    accumulator.observe_energy(end, 110)
    assert accumulator.result == CostResult(0.1, 5, 10, False)


@pytest.mark.parametrize("raw", [
    None, [], {"unexpected": True}, {"module": "module_2+module_3"},
    {"module": "module_2", "regular_network": 0.1},
    {"module": "module_2", "regular_network": 0.1, "price_includes_network": 1},
    {"windows": {}}, {"active_quarters": "1,4"}, {"windows": [{}] * 17},
    config(tariff_year=True), config(tariff_year=None), config(tariff_year=2023),
    config(tariff_year=9999), config(active_quarters=[1]),
    config(active_quarters=[1, 1]), config(active_quarters=[True, 4]),
    config(active_quarters=[0, 4]), config(active_quarters=[1, 5]),
    config(active_quarters=[1, 2, 3, 4, 1]),
    config(high_network=0.1), config(low_network=-1), config(low_network=0.1),
    config(windows=[]), config(windows=[window()]),
    config(windows=[window(), window("10:00", "11:00")]),
    config(windows=[window(), window("23:00", "01:00", "ht")]),
    config(windows=[window("00:00", "12:00"), window("12:00", "00:00", "ht")]),
    config(timezone="missing/timezone"), config(timezone="/absolute"),
    config(timezone="a" * 256), config(timezone=False),
])
def test_invalid_tariff_configuration_is_rejected(raw):
    with pytest.raises(ValueError, match=r"invalid|must|requires|overlap|is required|require low_network"):
        TariffConfig.parse(raw)


@pytest.mark.parametrize("number", [None, True, "0.1", float("nan"), float("inf"), 10**1000, 0, -1])
def test_regular_network_price_must_be_explicit_positive_finite(number):
    with pytest.raises(ValueError, match=r"finite number|regular_network must be positive"):
        TariffConfig.parse(config(regular_network=number))


@pytest.mark.parametrize("raw", [
    {}, None, {"start": "22:00", "end": "06:00", "rate": "st"},
    window("24:00"), window("6:00"), window("06:00:00"),
    window(False), window("06:00", "06:00"),
])
def test_invalid_windows_are_rejected(raw):
    with pytest.raises(ValueError, match="window"):
        TariffWindow.parse(raw)


@pytest.mark.parametrize("args", [(True, 60, "nt"), (-1, 60, "nt"), (0, 1440, "nt")])
def test_direct_window_constructor_is_validated(args):
    with pytest.raises(ValueError, match="window minutes out of range"):
        TariffWindow(*args)


def test_direct_tariff_constructor_cannot_bypass_validation():
    with pytest.raises(ValueError, match="module"):
        TariffConfig(module="invalid")
    raw = config()
    raw["windows"] = ("invalid", "invalid")
    raw["active_quarters"] = (1, 4)
    with pytest.raises(ValueError, match="windows"):
        TariffConfig(**raw)
    raw["active_quarters"] = [1, 4]
    with pytest.raises(ValueError, match="active_quarters"):
        TariffConfig(**raw)


@pytest.mark.parametrize("timestamp", [-1, True, None, float("nan"), 253402214400])
def test_bad_price_timestamp_is_unknown(timestamp):
    assert TariffConfig().price_at(timestamp, 0.3) is None


@pytest.mark.parametrize(("start", "end"), [(1, 1), (2, 1), (-1, 1), (1, float("inf"))])
def test_invalid_price_interval_raises(start, end):
    with pytest.raises(ValueError, match=r"positive duration|out of range|finite number"):
        TariffConfig().average_price(start, end, 0.3)


def test_too_large_tariff_interval_and_transition_overflow_are_unknown(monkeypatch):
    tariff = TariffConfig.parse(config())
    start = at("2026-01-01T00:00:00+00:00")
    assert tariff.average_price(start, start + MAX_TARIFF_INTERVAL + 1, 0) is None
    monkeypatch.setattr(charging_cost, "MAX_PRICE_CHANGES", 2)
    assert tariff.average_price(start, start + 86400, 0) is None


def test_network_price_arithmetic_overflow_is_unknown():
    tariff = TariffConfig.parse({"module": "module_2", "regular_network": 1e308, "price_includes_network": False})
    assert tariff.price_at(0, 1.79e308) is None


def test_cost_result_roundtrip_supports_negative_cost_and_strict_unknown():
    for result in (CostResult(-2, 0, 10, True), CostResult(2, 1, 10, False), CostResult(0, 0, 0, False)):
        assert CostResult.parse(result.as_dict()) == result
        assert result.cost == (-2 if result.complete else None)
        assert isinstance(result.known_cost, float)
        assert isinstance(result.energy_kwh, float)


@pytest.mark.parametrize("raw", [
    None, {}, {"known_cost": 0, "unpriced_kwh": 0, "energy_kwh": 0, "complete": True, "extra": 0},
    {"known_cost": 0, "unpriced_kwh": 0, "energy_kwh": 0, "complete": 1},
    {"known_cost": True, "unpriced_kwh": 0, "energy_kwh": 0, "complete": True},
    {"known_cost": 0, "unpriced_kwh": -1, "energy_kwh": 0, "complete": False},
    {"known_cost": 0, "unpriced_kwh": 1, "energy_kwh": 0, "complete": False},
    {"known_cost": 0, "unpriced_kwh": 1, "energy_kwh": 1, "complete": True},
    {"known_cost": 1, "unpriced_kwh": 0, "energy_kwh": 0, "complete": True},
])
def test_corrupt_completed_results_are_rejected(raw):
    with pytest.raises(ValueError, match=r"cost result|finite number"):
        CostResult.parse(raw)


def test_cost_accumulator_splits_price_events_and_does_not_reprice_old_intervals():
    accumulator = CostAccumulator()
    assert accumulator.result.cost is None
    accumulator.sample(0, 100, 0.2)
    accumulator.set_price(25, 0.4)
    accumulator.sample(100, 110, 0.6)
    assert accumulator.result.cost == pytest.approx(3.5)
    accumulator.observe_energy(200, 120)
    assert accumulator.result.cost == pytest.approx(9.5)
    assert accumulator.result.energy_kwh == 20
    assert not accumulator._pending


def test_missing_price_is_only_unknown_for_energy_in_the_missing_interval():
    accumulator = CostAccumulator()
    accumulator.sample(0, 100, 0.2)
    accumulator.set_price(25, None)
    accumulator.set_price(75, 0.4)
    accumulator.observe_energy(100, 110)
    assert accumulator.result.known_cost == pytest.approx(1.5)
    assert accumulator.result.unpriced_kwh == 5
    assert accumulator.result.energy_kwh == 10
    assert not accumulator.result.complete
    accumulator.observe_energy(200, 120)
    assert accumulator.result.known_cost == pytest.approx(5.5)
    assert accumulator.result.unpriced_kwh == 5
    assert accumulator.result.cost is None


def test_energy_without_any_price_is_unknown_and_zero_energy_needs_no_price():
    accumulator = CostAccumulator()
    accumulator.observe_energy(0, 100)
    accumulator.observe_energy(100, 100)
    assert accumulator.result.cost == 0
    accumulator.observe_energy(200, 110)
    assert accumulator.result == CostResult(0, 10, 10, False)


def test_baseline_and_same_timestamp_prices_use_last_value_without_a_zero_duration_interval():
    accumulator = CostAccumulator()
    accumulator.set_price(0, 0.1)
    accumulator.sample(0, 100, 0.2)
    accumulator.set_price(0, 0.3)
    accumulator.set_price(25, 0.4)
    accumulator.set_price(25, 0.5)
    accumulator.set_price(26, 0.5)
    assert len(accumulator._pending) == 1
    accumulator.observe_energy(100, 110)
    assert accumulator.result.cost == pytest.approx(4.5)
    accumulator.observe_energy(100, 110)
    assert accumulator.result.cost == pytest.approx(4.5)


@pytest.mark.parametrize("bad_reading", [None, True, -1, float("nan"), float("inf"), "100", 10**1000])
def test_failed_meter_read_invalidates_the_gap_without_losing_the_known_energy_delta(bad_reading):
    accumulator = CostAccumulator()
    accumulator.sample(0, 100, 0.2)
    accumulator.observe_energy(25, bad_reading)
    accumulator.set_price(50, 0.4)
    accumulator.observe_energy(100, 110)
    assert accumulator.result == CostResult(0, 10, 10, False)
    accumulator.observe_energy(200, 120)
    assert accumulator.result == CostResult(4, 10, 20, False)


def test_missing_initial_meter_sample_does_not_claim_a_complete_cycle():
    accumulator = CostAccumulator()
    accumulator.sample(0, None, 0.2)
    accumulator.observe_energy(10, 100)
    accumulator.observe_energy(100, 110)
    assert accumulator.result == CostResult(2, 0, 10, False)


def test_meter_reset_does_not_interpret_reset_value_as_new_consumption():
    accumulator = CostAccumulator()
    accumulator.sample(0, 100, 0.2)
    accumulator.observe_energy(100, 110)
    accumulator.observe_energy(200, 5)
    accumulator.observe_energy(300, 10)
    assert accumulator.result == CostResult(3, 0, 15, False)


@pytest.mark.parametrize("bad_time", [-1, True, float("nan"), 253402214400, 5])
@pytest.mark.parametrize("method", ["set_price", "observe_energy"])
def test_invalid_or_reversed_time_does_not_reprice_history(bad_time, method):
    accumulator = CostAccumulator()
    accumulator.sample(10, 100, 0.2)
    getattr(accumulator, method)(bad_time, 0.3 if method == "set_price" else 110)
    accumulator.observe_energy(100, 120)
    assert accumulator.result == CostResult(0, 20, 20, False)


def test_large_gap_and_energy_increase_at_same_timestamp_are_incomplete():
    accumulator = CostAccumulator(max_gap=100)
    accumulator.sample(0, 100, 0.2)
    accumulator.observe_energy(101, 110)
    accumulator.observe_energy(101, 120)
    assert accumulator.result == CostResult(0, 20, 20, False)


def test_price_change_memory_is_bounded_and_overflow_marks_the_interval_unknown():
    accumulator = CostAccumulator(max_gap=1000)
    accumulator.sample(0, 100, 0.2)
    for timestamp in range(1, MAX_PRICE_CHANGES + 20):
        accumulator.set_price(timestamp, float(timestamp))
        assert len(accumulator._pending) <= MAX_PRICE_CHANGES
    accumulator.observe_energy(500, 110)
    assert accumulator.result == CostResult(0, 10, 10, False)


@pytest.mark.parametrize("gap", [0, -1, True, None, float("nan"), MAX_TARIFF_INTERVAL + 1])
def test_invalid_maximum_gap_is_rejected(gap):
    with pytest.raises(ValueError, match=r"max_gap out of range|finite number"):
        CostAccumulator(max_gap=gap)


def test_reset_drops_prior_prices_sums_and_invalid_state():
    accumulator = CostAccumulator()
    accumulator.sample(0, 100, None)
    accumulator.observe_energy(100, 110)
    accumulator.reset()
    assert accumulator.result == CostResult(0, 0, 0, False)
    accumulator.sample(0, 100, -0.1)
    accumulator.observe_energy(100, 110)
    assert accumulator.result == CostResult(-1, 0, 10, True)


def test_module_three_schedule_and_dynamic_base_price_are_combined_independently():
    accumulator = CostAccumulator(TariffConfig.parse(config()), max_gap=7200)
    start = at("2026-01-01T20:00:00+00:00")
    accumulator.sample(start, 100, 0.1)
    accumulator.set_price(start + 1800, 0.2)
    accumulator.observe_energy(start + 7200, 120)
    # 30min: .1+.1; 30min: .2+.1; 60min: .2+.02.
    assert accumulator.result.cost == pytest.approx(4.7)


def test_finite_input_arithmetic_overflow_never_emits_infinite_money_or_energy():
    accumulator = CostAccumulator()
    accumulator.sample(0, 0, 1e308)
    accumulator.observe_energy(10, 1)
    accumulator.observe_energy(20, 2)
    assert accumulator.result == CostResult(1e308, 1, 2, False)
    accumulator.reset()
    accumulator.sample(0, 0, 1e308)
    accumulator.observe_energy(10, 1e308)
    assert accumulator.result == CostResult(0, 1e308, 1e308, False)
    accumulator.observe_energy(20, 0)
    accumulator.observe_energy(30, 1e308)
    assert accumulator.result == CostResult(0, 1e308, 1e308, False)


def test_unknown_interval_stays_incomplete_even_if_its_energy_underflows_to_zero():
    accumulator = CostAccumulator()
    accumulator.sample(0, 0, 0)
    accumulator.set_price(99, None)
    accumulator.observe_energy(100, 1e-323)
    assert accumulator.result.unpriced_kwh == 0
    assert accumulator.result.cost is None
