"""Protect one-trip intent and the wallbox command boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.button import (
    async_setup_entry as setup_buttons,
)
from custom_components.mennekes_amtron_rtu.datetime import (
    async_setup_entry as setup_datetime,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.energy_goal import EnergyGoal
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.options_flow import (
    _timed_errors,
)
from custom_components.mennekes_amtron_rtu.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.timed_goal_observer import (
    TimedGoalObserver,
    capacity_from_state,
    departure_time,
)
from tests.ha_fakes import build_runtime, collect

NOW = datetime(2026, 11, 7, 6, tzinfo=UTC)


def state(entity: str, value: str, **attributes: object) -> SimpleNamespace:
    return SimpleNamespace(entity_id=entity, state=value, attributes=attributes)


def test_one_trip_goal_round_trip_and_cancelled_entity_does_not_rearm() -> None:
    goal = TimedGoal()
    assert goal.set(NOW + timedelta(hours=1), "entity:sensor.departure:time")
    assert TimedGoal.restore(goal.snapshot()).snapshot() == goal.snapshot()
    assert goal.set(None, goal.source)
    assert goal.deadline is None
    assert goal.source == "entity:sensor.departure:time"
    assert not goal.set(None, goal.source)
    with pytest.raises(ValueError, match="timezone"):
        goal.set(NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="fields"):
        TimedGoal.restore({"deadline": None})
    for raw in (
        {"deadline": 123, "source": None},
        {"deadline": None, "source": 1},
        {"deadline": "not-a-date", "source": None},
        {"deadline": "2026-11-07T06:00:00", "source": None},
    ):
        with pytest.raises(ValueError, match="invalid departure"):
            TimedGoal.restore({**raw, "finished": None})
    with pytest.raises(ValueError, match="source"):
        goal.set(NOW, "")


def test_time_and_capacity_sources_need_dated_valid_data() -> None:
    assert departure_time(None) is None
    assert departure_time(state("sensor.departure", NOW.isoformat())) == NOW
    assert departure_time(state("sensor.departure", "07:30")) is None
    assert departure_time(state("sensor.departure", "2026-11-07T07:30:00")) is None
    assert departure_time(state("input_datetime.trip", "2026-11-07 07:30:00", timestamp=NOW.timestamp())) == NOW
    assert departure_time(state("input_datetime.trip", "2026-11-07 07:30:00")) is None
    assert departure_time(state("input_datetime.trip", "x", timestamp=float("inf"))) is None
    assert departure_time(state("input_datetime.trip", "x", timestamp=1e100)) is None
    assert departure_time(state("sensor.departure", NOW.isoformat(), restored=True)) is None
    assert capacity_from_state(state("sensor.capacity", "80000", unit_of_measurement="Wh")) == 80
    assert capacity_from_state(state("sensor.capacity", "80", unit_of_measurement="kWh")) == 80
    assert capacity_from_state(state("sensor.capacity", "80")) is None
    assert capacity_from_state(state("sensor.capacity", "unknown", unit_of_measurement="kWh")) is None
    assert capacity_from_state(state("sensor.capacity", "80", unit_of_measurement="kWh", restored=True)) is None
    assert capacity_from_state(None) is None
    assert capacity_from_state(state("sensor.capacity", "nan", unit_of_measurement="kWh")) is None


@pytest.mark.parametrize(("values", "error"), [
    ({"timed_capacity_entity": "switch.bad"}, "timed_source_invalid"),
    ({"timed_departure_entity": "switch.bad"}, "timed_source_invalid"),
    ({"timed_calendar_entity": "sensor.bad"}, "timed_source_invalid"),
    ({"timed_calendar_title": "Trip"}, "timed_calendar_required"),
    ({"timed_calendar_entity": "calendar.trip"}, "timed_calendar_title_required"),
    ({"timed_capacity_kwh": 0}, "timed_capacity_invalid"),
    ({"timed_capacity_kwh": float("nan")}, "timed_capacity_invalid"),
])
def test_timed_options_reject_ambiguous_or_unsafe_sources(values: dict, error: str) -> None:
    assert error in _timed_errors(values).values()


def test_timed_options_accept_separate_fixed_and_dynamic_sources() -> None:
    fixed = {"vehicle_soc_entity": "sensor.soc", "timed_capacity_kwh": 80,
             "timed_calendar_entity": "calendar.trip", "timed_calendar_title": "Departure"}
    assert _timed_errors(fixed) == {}
    assert _timed_errors({**fixed, "timed_departure_entity": "sensor.time"}) == {}
    dynamic = {"vehicle_soc_entity": "sensor.soc", "timed_capacity_entity": "sensor.cap",
               "timed_departure_entity": "input_datetime.trip"}
    assert _timed_errors(dynamic) == {}



class FakeStore:
    def __init__(self, *_args: object) -> None:
        self.saved: list[dict[str, str | None]] = []

    async def async_save(self, goal: TimedGoal) -> None:
        self.saved.append(goal.snapshot())

    async def async_load(self) -> TimedGoal:
        return TimedGoal()


class FakeControl:
    def __init__(self, data: WallboxData) -> None:
        self.soc_goal = SimpleNamespace(status="active", target_percent=80.0)
        self.energy_goal = EnergyGoal()
        self.max_charging_current = 16.0
        self.read_only = False
        self.energy_goal_stop_requested = False
        self.pending_action = None
        self.is_paused: bool | None = False
        self.commands: list[tuple[str, float | None]] = []
        self.data = data

    async def async_pause_charging(self) -> None:
        self.commands.append(("pause", None))
        self.is_paused = True
        self.data.values["charging_current_ems"] = 1.0

    async def async_set_charging_current(self, value: float) -> None:
        self.commands.append(("current", value))
        self.is_paused = False
        self.data.values["charging_current_ems"] = value

    async def async_cancel_timed_current(self, _owner: str) -> None:
        pass

    async def async_set_timed_current(self, current: float | None, *, deadline: datetime, owner: str) -> None:
        assert deadline > NOW
        assert owner.startswith("timed:")
        if current is None:
            await self.async_pause_charging()
        else:
            await self.async_set_charging_current(current)


def setup_observer(monkeypatch: pytest.MonkeyPatch) -> tuple[TimedGoalObserver, FakeControl, SimpleNamespace]:
    data = WallboxData(values={
        "charging_current_ems": 16.0,
        "switched_phases": 0,
        "grid_phases_connected": 2,
        "detected_ev_phases": 3,
        "evse_state": EvseState.CHARGING,
        "cp_state": CpState.C2,
    })
    control = FakeControl(data)
    coordinator = SimpleNamespace(
        current_data=lambda: data, last_update_success=True,
        async_update_listeners=lambda: None, async_add_listener=lambda _fn: lambda: None,
    )
    runtime = SimpleNamespace(control=control, coordinator=coordinator,
                              vehicle_soc=None, vehicle_telemetry=None,
                              identity=SimpleNamespace(serial_number="SERIAL", layout_version=310))
    entry = SimpleNamespace(
        entry_id="entry", runtime_data=runtime,
        options={"vehicle_soc_entity": "sensor.soc", "timed_capacity_kwh": 80},
    )
    hass = SimpleNamespace(
        states={"sensor.soc": state("sensor.soc", "20", unit_of_measurement="%")},
        data={}, services=SimpleNamespace(async_call=AsyncMock()),
    )
    hass.async_create_task = lambda coro, _name, **_kwargs: asyncio.create_task(coro)
    monkeypatch.setattr(observer_module, "TimedGoalStore", FakeStore)
    observer = TimedGoalObserver(hass, entry)
    # Unit cases configure this observer's captured options. A real HA options
    # update replaces the entry mapping and creates a new observer on reload.
    entry.options = observer._options
    monkeypatch.setattr(observer, "request_evaluate", lambda: None)
    monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: NOW)
    monkeypatch.setattr(observer_module, "time", SimpleNamespace(monotonic=lambda: 0.0))
    return observer, control, hass


def test_timed_goal_source_updates_and_explicit_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        future = NOW + timedelta(hours=8)
        observer._entry.options["timed_departure_entity"] = "sensor.departure"
        hass.states["sensor.departure"] = state("sensor.departure", future.isoformat())
        await observer._evaluate()
        assert observer.goal.deadline == future
        await observer.async_set_deadline(None)
        assert observer.goal.deadline is None
        await observer._evaluate()
        assert observer.goal.deadline is None
        assert observer.status == "disabled"
        assert len(observer._store.saved) == 2
        await observer.async_set_deadline(None)
        assert len(observer._store.saved) == 2
        with pytest.raises(Exception, match="future"):
            await observer.async_set_deadline(NOW - timedelta(seconds=1))
        with pytest.raises(Exception, match="timezone"):
            await observer.async_set_deadline(NOW.replace(tzinfo=None))
        with pytest.raises(Exception, match="source"):
            await observer.async_set_deadline(NOW + timedelta(hours=1), source="")

    asyncio.run(run())


def test_timed_plan_pauses_then_resumes_even_a_manual_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=24))
        await observer._evaluate()
        assert observer.status == "paused_until_start"
        assert control.commands == [("pause", None)]
        # The user deliberately leaves Pause on; this timed plan is allowed to
        # lift it for the selected trip. The plain SoC target does not.
        later = NOW + timedelta(hours=12)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: later)
        monkeypatch.setattr(observer_module.time, "monotonic", lambda: 12 * 3600.0)
        await observer._evaluate()
        assert control.commands[-1][0] == "current"
        assert 6.0 <= (control.commands[-1][1] or 0) <= 7.0
        assert observer.status == "paced"

    asyncio.run(run())


def test_missing_data_and_reached_goal_never_issue_a_current(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        del hass.states["sensor.soc"]
        await observer._evaluate()
        assert observer.status == "waiting_for_soc"
        assert control.commands == []
        hass.states["sensor.soc"] = state("sensor.soc", "80", unit_of_measurement="%")
        control.soc_goal.status = "reached"
        await observer._evaluate()
        assert observer.status == "reached"
        assert control.commands == []

    asyncio.run(run())


def test_invalid_capacity_read_only_and_queue_do_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        del observer._entry.options["timed_capacity_kwh"]
        await observer._evaluate()
        assert observer.status == "waiting_for_capacity"
        observer._entry.options["timed_capacity_entity"] = "sensor.capacity"
        hass.states["sensor.capacity"] = state("sensor.capacity", "80000", unit_of_measurement="Wh")
        observer._runtime.coordinator.last_update_success = False
        await observer._evaluate()
        assert observer.status == "waiting_for_device"
        observer._runtime.coordinator.last_update_success = True
        control.read_only = True
        await observer._evaluate()
        assert observer.status == "read_only"
        control.read_only = False
        control.pending_action = "resume"
        await observer._evaluate()
        assert observer.status == "queued"
        assert control.commands == []
        control.soc_goal.status = "disabled"
        await observer._evaluate()
        assert observer.status == "waiting_for_target"
        control.soc_goal.status = "active"
        control.pending_action = None
        control.energy_goal_stop_requested = True
        await observer._evaluate()
        assert observer.status == "waiting_for_device"
        assert control.commands == []

    asyncio.run(run())


def test_expired_plan_is_consumed_without_resuming_another_charge(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(minutes=1))
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: NOW + timedelta(minutes=2))
        control.data.values["charging_current_ems"] = 8.0
        await observer._evaluate()
        assert observer.status == "late"
        assert observer.goal.deadline is None
        assert control.commands == []
        await observer._evaluate()
        assert observer.status == "late"
        control.is_paused = True
        await observer._evaluate()
        assert control.commands == []
        assert observer.goal.finished == "late"
        await observer.async_set_deadline(NOW + timedelta(hours=8))
        await observer._evaluate()
        assert control.commands[-1][0] == "current"

    asyncio.run(run())


def test_current_write_guard_does_not_spam_the_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        await observer._evaluate()
        assert control.commands == [("current", 9.7)]
        control.data.values["charging_current_ems"] = 7.0
        await observer._evaluate()
        assert control.commands == [("current", 9.7)]
        monkeypatch.setattr(observer_module.time, "monotonic", lambda: 301.0)
        await observer._evaluate()
        assert control.commands[-1] == ("current", 9.7)
        assert len(control.commands) == 2

    asyncio.run(run())


def test_observer_lifecycle_and_write_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        unsubs: list[str] = []
        observer, _control, _hass = setup_observer(monkeypatch)
        monkeypatch.setattr(observer_module, "async_track_state_change_event",
                            lambda *_args: lambda: unsubs.append("state"))
        monkeypatch.setattr(observer_module, "async_track_time_interval",
                            lambda *_args: lambda: unsubs.append("time"))
        observer.request_evaluate = observer_module.TimedGoalObserver.request_evaluate.__get__(observer)
        await observer.async_start()
        await observer._task
        assert observer.status == "disabled"

        async def failed() -> None:
            raise observer_module.HomeAssistantError("bus busy")

        monkeypatch.setattr(observer, "_evaluate", failed)
        observer.request_evaluate()
        await observer._task
        assert observer.status == "write_pending"
        observer.request_evaluate()
        await observer._task
        assert observer.status == "write_pending"

        async def broken() -> None:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(observer, "_evaluate", broken)
        observer.request_evaluate()
        await observer._task
        assert observer.status == "error"
        await observer.async_stop()
        assert "state" in unsubs
        assert "time" in unsubs
        observer.request_evaluate()
        assert observer._task.done()

    asyncio.run(run())


def test_phase_fallback_and_short_pause_window(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=24))
        control.data.values["switched_phases"] = 1
        assert observer._phases() == 1
        control.data.values["switched_phases"] = None
        control.data.values["grid_phases_connected"] = None
        assert observer._phases() == 1
        control.data.values["switched_phases"] = 0
        control.data.values["grid_phases_connected"] = 2
        assert observer._phases() == 3
        await observer._evaluate()
        assert control.is_paused is True
        await observer._evaluate()
        assert control.commands == [("pause", None)]

    asyncio.run(run())


def test_calendar_button_selects_one_exact_timed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        observer._entry.options.update({
            "timed_calendar_entity": "calendar.trips",
            "timed_calendar_title": "Departure",
        })
        events = [
            {"start": (NOW + timedelta(hours=3)).isoformat(), "summary": "Other"},
            {"start": (NOW + timedelta(hours=2)).isoformat(), "summary": "Departure"},
            {"start": (NOW + timedelta(hours=1)).isoformat(), "summary": "Departure"},
        ]
        hass.services.async_call.return_value = {"calendar.trips": {"events": events}}
        await observer.async_use_next_calendar_event()
        assert observer.goal.deadline == NOW + timedelta(hours=1)
        assert observer.goal.source is None
        # A second event does not replace the selected trip on the next poll.
        await observer._evaluate()
        assert observer.goal.deadline == NOW + timedelta(hours=1)

    asyncio.run(run())


def test_calendar_requires_a_matching_timed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        with pytest.raises(Exception, match="calendar"):
            await observer.async_use_next_calendar_event()
        observer._entry.options.update({
            "timed_calendar_entity": "calendar.trips", "timed_calendar_title": "Departure",
        })

        hass.services.async_call.return_value = {"calendar.trips": {"events": [
            {"start": NOW.date().isoformat(), "summary": "Departure"},
        ]}}
        with pytest.raises(Exception, match="No matching"):
            await observer.async_use_next_calendar_event()
        hass.services.async_call.return_value = None
        with pytest.raises(Exception, match="valid event list"):
            await observer.async_use_next_calendar_event()
        hass.services.async_call.return_value = {"calendar.trips": {"events": [
            {"start": "2026-11-07Tbad", "summary": "Departure"},
            {"start": "2026-11-07T07:00:00", "summary": "Departure"},
            {"start": (NOW + timedelta(days=15)).isoformat(), "summary": "Departure"},
            "not an event",
        ]}}
        with pytest.raises(Exception, match="No matching"):
            await observer.async_use_next_calendar_event()

    asyncio.run(run())


def test_departure_controls_show_saved_trip_and_delegate_actions() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        goal = TimedGoal()
        goal.set(NOW + timedelta(hours=8))
        timed = SimpleNamespace(
            goal=goal, async_set_deadline=AsyncMock(),
            async_use_next_calendar_event=AsyncMock(),
            vehicle_departure_configured=False,
        )
        entry.runtime_data.timed_goal = timed
        entry.options["timed_calendar_entity"] = "calendar.trips"
        datetimes: dict[str, object] = {}
        buttons: dict[str, object] = {}
        await setup_datetime(hass, entry, lambda items: datetimes.update(collect(items)))
        await setup_buttons(hass, entry, lambda items: buttons.update(collect(items)))
        departure = datetimes["timed_departure"]
        assert departure.native_value == NOW + timedelta(hours=8)
        assert departure.available
        await departure.async_set_value(NOW + timedelta(hours=9))
        timed.async_set_deadline.assert_awaited_once_with(NOW + timedelta(hours=9))
        next_event = buttons["timed_next_calendar_event"]
        await next_event.async_press()
        timed.async_use_next_calendar_event.assert_awaited_once()
        await buttons["timed_clear_departure"].async_press()
        timed.async_set_deadline.assert_any_await(None)
        entry.runtime_data.timed_goal = None
        assert departure.native_value is None
        await departure.async_set_value(NOW + timedelta(hours=10))
        await next_event.async_press()
        await buttons["timed_clear_departure"].async_press()
        await entry.runtime_data.control.async_shutdown()

    asyncio.run(run())
