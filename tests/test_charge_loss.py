"""Only matching, complete and physically possible charges enter loss statistics."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_loss import LossStatistics
from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)


def session(index=1, *, energy=20.0) -> CompletedSession:
    offset = 2000.0 * index
    return CompletedSession(
        str(UUID(int=index)), offset, offset + 1200,
        offset + 100, offset + 105, offset + 1000, offset + 1005, energy,
    )


def stats() -> LossStatistics:
    result = LossStatistics()
    result.configure_source("sensor.car_last_charge|start|end")
    return result


def accept(model, item, energy=18.0, **values):
    return model.apply(item, **{
        "vehicle_kwh": energy, "association": "changed_value", **values,
    })


def test_unconfigured_model_is_inert_and_has_no_fabricated_zero():
    model = LossStatistics()
    assert model.status == "disabled"
    assert model.last_loss_percent is None
    assert model.average_loss_percent is None
    assert model.last_session_id is None
    assert model.sample_count == 0
    assert model.association is None
    assert model.wallbox_kwh is None
    assert model.vehicle_kwh is None
    assert not model.configure_source(None)
    assert not model.observe_session(session())
    assert not accept(model, session())
    assert LossStatistics.restore(model.snapshot()).snapshot() == model.snapshot()


def test_weighted_average_and_correction_of_latest_session_without_recounting():
    model = stats()
    first = session(1, energy=10)
    assert accept(model, first, 9)
    assert model.last_loss_percent == pytest.approx(10)
    assert model.average_loss_percent == pytest.approx(10)
    assert model.sample_count == 1
    assert not accept(model, first, 9)
    second = session(2, energy=30)
    assert model.observe_session(second)
    assert model.last_loss_percent is None
    assert model.average_loss_percent == pytest.approx(10)
    assert model.status == "waiting_for_vehicle"
    assert accept(model, second, 24)
    assert model.last_loss_percent == pytest.approx(20)
    assert model.average_loss_percent == pytest.approx(17.5)  # 7 / 40, not mean(10,20).
    assert model.sample_count == 2
    assert accept(model, second, 27)
    assert model.last_loss_percent == pytest.approx(10)
    assert model.average_loss_percent == pytest.approx(10)
    assert model.sample_count == 2
    for _ in range(10):
        assert not accept(model, second, 27)
    assert model.last_session_id == second.session_id
    restored = LossStatistics.restore(model.snapshot())
    assert restored.snapshot() == model.snapshot()
    assert restored.average_loss_percent == pytest.approx(10)
    assert not accept(restored, second, 27)


@pytest.mark.parametrize("association", ["changed_value", "manual", "history"])
def test_owner_supplies_association_and_current_comparison_details(association):
    model = stats()
    assert model.apply(session(), 18, association=association)
    assert model.association == association
    assert model.wallbox_kwh == 20
    assert model.vehicle_kwh == 18
    assert LossStatistics.restore(model.snapshot()).association == association
    model.observe_session(session(2))
    assert model.association is None
    assert model.wallbox_kwh is None
    assert model.vehicle_kwh is None


@pytest.mark.parametrize("association", [None, [], True, "guessed", ""])
def test_invalid_association_does_not_mutate_statistics(association):
    model = stats()
    before = model.snapshot()
    with pytest.raises(ValueError, match="association"):
        model.apply(session(), 18, association=association)
    assert model.snapshot() == before


def test_historical_corrections_and_overlapping_sessions_are_ignored():
    model = stats()
    first, second = session(), session(2)
    accept(model, first)
    accept(model, second)
    original = model.snapshot()
    assert not accept(model, first, 15)
    assert not model.observe_session(first)
    overlap = replace(second, session_id=str(UUID(int=3)), disconnected_at=second.disconnected_at + 1)
    assert not accept(model, overlap)
    assert model.snapshot() == original
    same_end = replace(second, session_id=str(UUID(int=4)))
    assert not model.observe_session(same_end)


def test_same_identifier_cannot_rewrite_wallbox_measurements():
    model = stats()
    item = session()
    accept(model, item)
    with pytest.raises(ValueError, match="identifier changed"):
        accept(model, replace(item, wallbox_kwh=30))
    assert model.last_loss_percent == pytest.approx(10)


def test_missing_complete_session_hides_last_result_but_keeps_valid_average():
    model = stats()
    item = session()
    accept(model, item)
    assert model.observe_session(None)
    assert model.status == "waiting_for_session"
    assert model.last_loss_percent is None
    assert model.last_session_id is None
    assert model.average_loss_percent == pytest.approx(10)
    assert not model.observe_session(None)
    restored = LossStatistics.restore(model.snapshot())
    assert restored.average_loss_percent == pytest.approx(10)
    assert restored.observe_session(item)
    assert restored.status == "matched"
    assert restored.sample_count == 1
    assert not restored.observe_session(item)


def test_missing_vehicle_input_never_counts_as_zero_loss():
    model = stats()
    accept(model, session())
    newer = session(2)
    assert accept(model, newer, vehicle_kwh=None)
    assert model.status == "waiting_for_vehicle"
    assert model.last_loss_percent is None
    assert model.average_loss_percent == pytest.approx(10)
    assert model.sample_count == 1
    assert not accept(model, newer, vehicle_kwh=None)


@pytest.mark.parametrize("value", [True, "18", -1, float("nan"), float("inf"), 10**1000])
def test_invalid_vehicle_values_are_not_measurements(value):
    model = stats()
    assert accept(model, session(), vehicle_kwh=value)
    assert model.status == "invalid"
    assert model.last_loss_percent is None
    assert model.average_loss_percent is None
    assert model.sample_count == 0
    assert not accept(model, session(), vehicle_kwh=value)


@pytest.mark.parametrize("energy", [0, 20.001, 1000])
def test_impossible_vehicle_energy_is_not_clamped_to_zero_loss(energy):
    model = stats()
    accept(model, session(), energy)
    assert model.status == "invalid"
    assert model.last_loss_percent is None
    assert model.average_loss_percent is None


def test_real_equal_energy_is_a_valid_zero_loss_measurement():
    model = stats()
    accept(model, session(), 20)
    assert model.status == "matched"
    assert model.last_loss_percent == 0
    assert model.average_loss_percent == 0
    assert model.sample_count == 1


def test_bad_current_report_hides_last_result_without_destroying_accepted_sums():
    model = stats()
    item = session()
    accept(model, item)
    assert accept(model, item, float("nan"))
    assert model.last_loss_percent is None
    assert model.average_loss_percent == pytest.approx(10)
    restored = LossStatistics.restore(model.snapshot())
    assert restored.status == "invalid"
    assert accept(restored, item, 19)
    assert restored.sample_count == 1
    assert restored.average_loss_percent == pytest.approx(5)


def test_changing_or_removing_source_resets_the_vehicle_specific_average():
    model = stats()
    accept(model, session())
    assert not model.configure_source(model.source)
    assert model.configure_source("sensor.other_vehicle|start|end")
    assert model.sample_count == 0
    assert model.average_loss_percent is None
    assert model.last_session_id is None
    assert model.status == "waiting_for_session"
    assert model.configure_source(None)
    assert model.status == "disabled"


@pytest.mark.parametrize("source", [False, 1, "", " ", "a" * 1025])
def test_invalid_source_does_not_discard_existing_statistics(source):
    model = stats()
    accept(model, session())
    snapshot = model.snapshot()
    with pytest.raises(ValueError, match="source"):
        model.configure_source(source)
    assert model.snapshot() == snapshot


def test_overflowing_aggregate_retains_previous_finite_totals():
    model = stats()
    accept(model, session(1, energy=1e308), 9e307)
    accept(model, session(2, energy=1e308), 9e307)
    assert model.status == "invalid"
    assert model.sample_count == 1
    assert model.average_loss_percent == pytest.approx(10)
    assert model.last_loss_percent is None
    assert LossStatistics.restore(model.snapshot()).snapshot() == model.snapshot()


@pytest.mark.parametrize("raw", [None, [], {}, "", {"source": "car"}])
def test_restore_rejects_wrong_shapes(raw):
    with pytest.raises(ValueError, match="fields"):
        LossStatistics.restore(raw)


@pytest.mark.parametrize("changes", [
    {"source": None}, {"status": "disabled"}, {"status": "waiting_for_session"},
    {"status": []}, {"status": "bogus"}, {"previous_count": True},
    {"previous_count": -1}, {"previous_count": 2**63 - 1},
    {"previous_count": 1}, {"previous_wallbox_kwh": 1},
    {"previous_vehicle_kwh": 1}, {"previous_wallbox_kwh": float("nan")},
    {"current_session": None}, {"last_match": None}, {"extra": True},
])
def test_restore_rejects_inconsistent_state(changes):
    model = stats()
    accept(model, session())
    with pytest.raises(ValueError, match=r"loss|session"):
        LossStatistics.restore(model.snapshot() | changes)


@pytest.mark.parametrize("match", [
    None, [], {}, {"session": {}},
])
def test_restore_rejects_invalid_matched_session_shape(match):
    model = stats()
    accept(model, session())
    raw = model.snapshot()
    raw["last_match"] = match
    with pytest.raises(ValueError, match=r"loss|session"):
        LossStatistics.restore(raw)


@pytest.mark.parametrize("changes", [
    {"vehicle_kwh": 21}, {"vehicle_kwh": False}, {"association": "guessed"},
    {"association": []}, {"extra": 1},
])
def test_restore_revalidates_saved_match_against_the_wallbox(changes):
    model = stats()
    accept(model, session())
    raw = model.snapshot()
    raw["last_match"].update(changes)
    with pytest.raises(ValueError, match=r"loss|session"):
        LossStatistics.restore(raw)


def test_restore_checks_session_order_and_identifier_consistency():
    model = stats()
    accept(model, session(2))
    saved = model.snapshot()
    for current in (
        session(1),
        replace(session(2), wallbox_kwh=21),
        replace(session(2), session_id=str(UUID(int=3)), disconnected_at=5300),
    ):
        raw = deepcopy(saved)
        raw["status"] = "waiting_for_vehicle"
        raw["current_session"] = current.as_dict()
        with pytest.raises(ValueError, match="order or totals"):
            LossStatistics.restore(raw)


def test_restore_checks_finite_sums_and_positive_historical_samples():
    model = stats()
    accept(model, session())
    saved = model.snapshot()
    for prefix in (
        {"previous_count": 1, "previous_wallbox_kwh": 1, "previous_vehicle_kwh": 0},
        {"previous_count": 1, "previous_wallbox_kwh": 1, "previous_vehicle_kwh": 2},
        {"previous_count": 1, "previous_wallbox_kwh": 1, "previous_vehicle_kwh": 1, "last_match": None},
    ):
        with pytest.raises(ValueError, match="statistics"):
            LossStatistics.restore(saved | prefix)
    saved["previous_count"] = 1
    saved["previous_wallbox_kwh"] = saved["previous_vehicle_kwh"] = 1e308
    saved["last_match"]["session"]["wallbox_kwh"] = 1e308
    saved["last_match"]["vehicle_kwh"] = 1e308
    saved["current_session"] = saved["last_match"]["session"]
    with pytest.raises(ValueError, match="order or totals"):
        LossStatistics.restore(saved)


def test_snapshot_is_independent_of_mutation_by_storage_callers():
    model = stats()
    accept(model, session())
    raw = model.snapshot()
    raw["last_match"]["vehicle_kwh"] = 0
    raw["current_session"]["wallbox_kwh"] = 1
    assert model.last_loss_percent == pytest.approx(10)


def test_aggregate_has_constant_size_and_no_session_history():
    model = stats()
    for index in range(1, 101):
        accept(model, session(index))
    assert model.sample_count == 100
    assert model.average_loss_percent == pytest.approx(10)
    assert set(model.snapshot()) == {
        "source", "previous_wallbox_kwh", "previous_vehicle_kwh", "previous_count",
        "last_match", "current_session", "status",
    }
    assert LossStatistics.restore(model.snapshot()).sample_count == 100
