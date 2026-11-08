"""Resolve optional BMW departure timers from proven local vehicle entities."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

import custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata as adapter
import custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.departure as module
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.departure import (
    next_timer_occurrence,
    read_bavarian_departure,
)
from tests.test_bavarian_history import VIN, vehicle_registry
from tests.test_timed_goal import NOW, setup_observer, state

PREFIX = "vehicle.cabin.climate.timers"


class FakeHass(SimpleNamespace):
    """HA registry getters cache by the identity of the HomeAssistant object."""

    __hash__ = object.__hash__


def local_vehicle() -> SimpleNamespace:
    registry = SimpleNamespace(entries={})
    registry.async_get_entity_id = lambda domain, platform, unique: (
        registry.entries.get(unique).entity_id if domain == "sensor" and platform == "bavariandata"
        and unique in registry.entries else None
    )
    registry.async_get = lambda entity_id: next(
        (entry for entry in registry.entries.values() if entry.entity_id == entity_id), None,
    )
    return FakeHass(
        data={dr.DATA_REGISTRY: vehicle_registry(), er.DATA_REGISTRY: registry},
        states={}, services=SimpleNamespace(async_call=AsyncMock()),
        config=SimpleNamespace(time_zone="Europe/Berlin"),
    )


def add_timer(hass: SimpleNamespace, name: str = "overwriteTimer", **parts: str) -> None:
    registry = hass.data[er.DATA_REGISTRY]
    for part, value in {"action": "activate", "hour": "8.0", "minute": "15", **parts}.items():
        descriptor = f"{PREFIX}.{name}.{part}"
        entity_id = f"sensor.{name}_{part}"
        registry.entries[f"{VIN}_{descriptor}"] = SimpleNamespace(entity_id=entity_id, device_id="vehicle")
        hass.states[entity_id] = state(entity_id, value, descriptor=descriptor)


def read(hass: SimpleNamespace, **changes: object) -> datetime | None:
    return read_bavarian_departure(
        hass, "vehicle", **{"now": NOW, "time_zone": "Europe/Berlin", **changes},
    )


def test_next_only_source_uses_vehicle_zone_without_cloud_or_entity_names() -> None:
    hass = local_vehicle()
    add_timer(hass)
    registry = hass.data[er.DATA_REGISTRY]
    descriptor = f"{PREFIX}.overwriteTimer.hour"
    registry.entries[f"{VIN}_{descriptor}"].entity_id = "sensor.renamed_by_owner"
    hass.states["sensor.renamed_by_owner"] = hass.states.pop("sensor.overwriteTimer_hour")
    assert read(hass) == NOW.replace(hour=7, minute=15)
    hass.services.async_call.assert_not_called()
    assert read(hass, now=NOW.replace(hour=10)) == NOW.replace(day=8, hour=7, minute=15)


def test_weekday_timers_require_explicit_days_and_choose_earliest() -> None:
    hass = local_vehicle()
    add_timer(hass, "weekdaysTimer1", hour="8", minute="00")
    add_timer(hass, "weekdaysTimer2", hour="9", minute="00")
    assert read(hass) is None
    assert read(hass, timer1_days=["sun"], timer2_days=["sat"]) == NOW.replace(hour=8)
    assert read(hass, timer1_days=["sun"]) == NOW.replace(day=8, hour=7)
    # A weekly occurrence that has already passed selects next week's day.
    assert read(hass, now=NOW.replace(hour=10), timer1_days=["sat"]) == NOW.replace(day=14, hour=7)


@pytest.mark.parametrize(("part", "value"), [
    ("action", "deactivate"), ("action", "no_action"), ("action", "active"),
    ("hour", "24"), ("minute", "60"), ("hour", "-1"), ("minute", "2.5"),
    ("hour", "NaN"), ("minute", "1e1"), ("minute", "True"),
])
def test_invalid_or_inactive_timer_is_ignored(part: str, value: str) -> None:
    hass = local_vehicle()
    add_timer(hass, **{part: value})
    assert read(hass) is None


@pytest.mark.parametrize("corruption", [
    "missing_registry", "missing_entry", "missing_state", "wrong_device", "wrong_descriptor", "restored", "unknown", "unavailable",
])
def test_only_available_identified_vehicle_states_have_authority(corruption: str) -> None:
    hass = local_vehicle()
    add_timer(hass)
    entity_id = "sensor.overwriteTimer_action"
    unique_id = f"{VIN}_{PREFIX}.overwriteTimer.action"
    if corruption == "missing_registry":
        del hass.data[er.DATA_REGISTRY].entries[unique_id]
    elif corruption == "missing_entry":
        hass.data[er.DATA_REGISTRY].async_get = Mock(return_value=None)
    elif corruption == "missing_state":
        del hass.states[entity_id]
    elif corruption == "wrong_device":
        hass.data[er.DATA_REGISTRY].entries[unique_id].device_id = "other_vehicle"
    elif corruption == "wrong_descriptor":
        hass.states[entity_id].attributes["descriptor"] = "other"
    elif corruption == "restored":
        hass.states[entity_id].attributes["restored"] = True
    else:
        hass.states[entity_id].state = corruption
    assert read(hass) is None


def test_missing_clock_part_unknown_vehicle_zone_or_registry_returns_no_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = local_vehicle()
    add_timer(hass)
    del hass.states["sensor.overwriteTimer_hour"]
    assert read(hass) is None
    add_timer(hass)
    assert read(hass, time_zone="Invalid/Zone") is None
    hass.data[dr.DATA_REGISTRY].async_get = Mock(return_value=None)
    assert read(hass) is None
    hass.data[dr.DATA_REGISTRY] = vehicle_registry()
    monkeypatch.setattr(module.er, "async_get", Mock(side_effect=RuntimeError("not ready")))
    assert read(hass) is None


@pytest.mark.parametrize(("hour", "minute", "weekdays"), [
    (True, 30, None), (24, 30, None), (8, False, None), (8, 60, None),
    (8, 30, []), (8, 30, ["monday"]), (8, 30, "mon"),
])
def test_invalid_clock_or_weekday_data_never_invents_a_schedule(hour, minute, weekdays) -> None:
    assert next_timer_occurrence(NOW, ZoneInfo("UTC"), hour, minute, weekdays) is None
    assert next_timer_occurrence(NOW.replace(tzinfo=None), ZoneInfo("UTC"), 8, 30) is None


def test_dst_gap_is_skipped_and_ambiguous_clock_uses_first_future_instant() -> None:
    zone = ZoneInfo("Europe/Berlin")
    before_gap = datetime(2026, 3, 29, 0, tzinfo=UTC)
    # 02:30 does not occur that Sunday. A weekly timer chooses next Sunday;
    # next-only cannot jump beyond its 24-hour limit.
    assert next_timer_occurrence(before_gap, zone, 2, 30) is None
    assert next_timer_occurrence(before_gap, zone, 2, 30, ["sun"]) == datetime(2026, 4, 5, 0, 30, tzinfo=UTC)
    before_fold = datetime(2026, 10, 25, 0, tzinfo=UTC)
    assert next_timer_occurrence(before_fold, zone, 2, 30) == before_fold.replace(minute=30)
    between_folds = before_fold.replace(hour=1)
    assert next_timer_occurrence(between_folds, zone, 2, 30) == between_folds.replace(minute=30)
    # A next-only timer is bounded by elapsed hours even on a 25-hour day.
    before_long_day = datetime(2026, 10, 24, 23, tzinfo=UTC)
    assert next_timer_occurrence(before_long_day, zone, 0, 30) is None


def test_vehicle_import_uses_local_options_once_and_retains_manual_override_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        hass.config = SimpleNamespace(time_zone="Europe/Berlin")
        hass.data[dr.DATA_REGISTRY] = vehicle_registry()
        observer._entry.options.update({
            "timed_bavarian_device": "vehicle", "timed_vehicle_time_zone": "Europe/Paris",
            "timed_timer1_days": ["mon"], "timed_timer2_days": ["tue"],
            "timed_departure_entity": "sensor.trip",
        })
        hass.states["sensor.trip"] = state("sensor.trip", (NOW + timedelta(hours=3)).isoformat())
        reader = Mock(return_value=NOW + timedelta(hours=2))
        monkeypatch.setattr(adapter, "read_bavarian_departure", reader)
        assert observer.vehicle_departure_available
        await observer.async_use_vehicle_departure()
        reader.assert_called_once_with(
            hass, "vehicle", now=NOW, time_zone="Europe/Paris", timer1_days=["mon"], timer2_days=["tue"],
        )
        await observer._evaluate()
        assert observer.goal.deadline == NOW + timedelta(hours=2)
        reader.return_value = None
        before = observer.goal
        with pytest.raises(ServiceValidationError):
            await observer.async_use_vehicle_departure()
        assert observer.goal is before
        observer._entry.options["timed_vehicle_provider"] = "none"
        assert not observer.vehicle_departure_available
        with pytest.raises(ServiceValidationError) as error:
            await observer.async_use_vehicle_departure()
        assert error.value.translation_key == "timed_vehicle_unavailable"
        assert observer.goal is before

    asyncio.run(run())


def test_calendar_and_timed_command_cancellation_preserve_new_trip_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        control.async_cancel_timed_current = AsyncMock()
        observer._entry.options.update({
            "timed_departure_entity": "sensor.trip", "timed_calendar_entity": "calendar.trip",
            "timed_calendar_title": "Trip",
        })
        hass.states["sensor.trip"] = state("sensor.trip", (NOW + timedelta(hours=3)).isoformat())
        hass.services.async_call.return_value = {"calendar.trip": {"events": [{
            "summary": "Trip", "start": (NOW + timedelta(hours=8)).isoformat(),
        }]}}
        await observer.async_use_next_calendar_event()
        await observer._evaluate()
        assert observer.goal.deadline == NOW + timedelta(hours=8)
        await observer.async_set_deadline(None)
        await observer._write_current(16, None)
        await observer._write_current(16, NOW)
        assert control.commands == [("current", 9.7)]
        await observer.async_stop()
        control.async_cancel_timed_current.assert_awaited_once_with(observer._command_owner)

    asyncio.run(run())


def test_unavailable_service_cancellation_does_not_abort_unload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        await observer._evaluate()
        control.async_cancel_timed_current = AsyncMock(side_effect=OSError("service offline"))
        await observer.async_stop()
        assert observer._stopping
        assert observer._unsubs == []

    asyncio.run(run())
