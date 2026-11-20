"""Protect one-trip intent and the wallbox command boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.datetime import (
    async_setup_entry as setup_datetime,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.departure_plan import DepartureState
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.options_flow import (
    _timed_errors,
)
from custom_components.mennekes_amtron_rtu.services import DEVICE_ACTIONS
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



def plan_state(trip: TimedGoal | None = None, **changes: object) -> dict:
    """The service's plan as it reports it."""
    return {
        "trip": (trip or TimedGoal()).snapshot(), "status": "disabled", "planned_start": None,
        "estimated_soc": None, "inputs": None, "scope": None, "confirmed": False, "forecast": None,
        "editor": "ha", "selection_vehicle": None, "edit_revision": "a" * 32, "withdrawn": False, **changes,
    }


class FakeControl:
    """The service control as the observer uses it: the service keeps the one trip."""

    @staticmethod
    def guest_active() -> bool:
        return False

    def __init__(self, data: WallboxData) -> None:
        self.read_only = False
        self.state: dict | None = plan_state()
        self.data = data
        self.trips: list[tuple[datetime | None, str | None, bool]] = []
        self.expected: list[dict | None] = []
        self.target_times: list[datetime | None] = []
        self.edits = 0

    @property
    def departure(self) -> DepartureState | None:
        return None if self.state is None else DepartureState.restore(self.state)

    async def async_set_departure_trip(
        self, deadline: datetime | None, source: str | None, *, withdraw: bool = False,
        expected_selection: dict | None = None,
    ) -> None:
        if expected_selection not in (None, {"edit_revision": (self.state or {}).get("edit_revision")}):
            # As the service does: the trip was edited meanwhile.
            raise HomeAssistantError("soc_goal_source_changed")
        self.trips.append((deadline, source, withdraw))
        self.expected.append(expected_selection)
        if self.state is not None:
            trip = TimedGoal.restore(self.state["trip"])
            trip.set(deadline, source)
            self.edits += 1
            self.state = {**self.state, "trip": trip.snapshot(), "edit_revision": f"{self.edits:032x}"}

    async def async_set_departure_inputs(self, inputs: object) -> None:
        raise AssertionError("no car, so no car data is handed over")

    async def async_set_target_time(self, deadline: datetime | None) -> None:
        self.target_times.append(deadline)


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
    observer = TimedGoalObserver(hass, entry)
    # Unit cases configure this observer's captured options. A real HA options
    # update replaces the entry mapping and creates a new observer on reload.
    entry.options = observer._options
    monkeypatch.setattr(observer, "request_evaluate", lambda: None)
    monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: NOW)
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
        # The cleared time keeps the source's mark: the same timestamp does not arm it again.
        await observer._evaluate()
        assert observer.goal.deadline is None
        assert len(_control.trips) == 2
        # Nothing changes: nothing is sent.
        await observer.async_set_deadline(None)
        assert len(_control.trips) == 2
        # While the service is still loading its plan, the trip is not known.
        _control.state = None
        await observer._evaluate()
        assert observer.status == "waiting_for_device"
        assert observer.goal.deadline is None
        assert len(_control.trips) == 2
        _control.state = plan_state()
        for wrong in (NOW - timedelta(seconds=1), NOW.replace(tzinfo=None)):
            with pytest.raises(HomeAssistantError) as refused:
                await observer.async_set_deadline(wrong)
            assert refused.value.translation_key == "target_time_not_future"
        with pytest.raises(Exception, match="source"):
            await observer.async_set_deadline(NOW + timedelta(hours=1), source="")

    asyncio.run(run())


def test_observer_lifecycle_and_write_failure_status(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    async def run() -> None:
        unsubs: list[str] = []
        observer, _control, _hass = setup_observer(monkeypatch)
        monkeypatch.setattr(observer_module, "async_track_state_change_event",
                            lambda *_args: lambda: unsubs.append("state"))
        monkeypatch.setattr(observer_module, "async_track_time_interval",
                            lambda *_args: lambda: unsubs.append("time"))
        observer.request_evaluate = observer_module.TimedGoalObserver.request_evaluate.__get__(observer)
        # The binding re-evaluates the plan when a car confirms or rests.
        observer._runtime.vehicle_binding = SimpleNamespace(
            resting=False, subscribe=lambda _listener: lambda: unsubs.append("binding"),
        )
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
        # A start that no longer holds leaves the display together with the status.
        notified = []
        monkeypatch.setattr(observer, "_notify", lambda: notified.append(observer.planned_start))
        observer.planned_start = datetime(2026, 11, 16, tzinfo=UTC)
        observer.forecast = object()
        observer._set_status("write_pending")
        observer._set_status("write_pending")
        assert notified == [None]
        # Nor does an estimate stay beside a status it was not made for.
        assert observer.forecast is None
        monkeypatch.undo()
        monkeypatch.setattr(observer, "_evaluate", failed)
        # The cause is named, not its text, which may differ every time.
        waiting = [r for r in caplog.records if "not handed to the service yet" in r.getMessage()]
        assert [(r.levelname, r.getMessage()) for r in waiting] == [
            ("WARNING", "Departure plan: not handed to the service yet (HomeAssistantError)"),
            ("DEBUG", "Departure plan: not handed to the service yet (HomeAssistantError)"),
        ]

        async def broken() -> None:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(observer, "_evaluate", broken)
        for _ in range(3):
            observer.request_evaluate()
            await observer._task
            assert observer.status == "error"
        # A fault that returns with every evaluation is reported once.
        reports = [r for r in caplog.records if "evaluation failed" in r.getMessage()]
        assert [(r.levelname, bool(r.exc_info)) for r in reports] == [
            ("ERROR", True), ("DEBUG", False), ("DEBUG", False),
        ]
        assert reports[0].getMessage() == "Departure plan: evaluation failed (RuntimeError)"
        await observer.async_stop()
        assert "state" in unsubs
        assert "time" in unsubs
        observer.request_evaluate()
        assert observer._task.done()

    asyncio.run(run())


def test_phase_fallback_without_a_detected_car(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        control.data.values["switched_phases"] = 1
        assert observer._phases() == 1
        control.data.values["switched_phases"] = None
        control.data.values["grid_phases_connected"] = None
        # A car never seen charging is planned with three phases.
        assert observer._phases() == 3
        control.data.values["switched_phases"] = 0
        control.data.values["grid_phases_connected"] = 2
        assert observer._phases() == 3

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
        with pytest.raises(HomeAssistantError) as refused:
            await observer.async_use_next_calendar_event()
        assert refused.value.translation_key == "calendar_not_configured"
        observer._entry.options.update({
            "timed_calendar_entity": "calendar.trips", "timed_calendar_title": "Departure",
        })

        hass.services.async_call.return_value = {"calendar.trips": {"events": [
            {"start": NOW.date().isoformat(), "summary": "Departure"},
        ]}}
        with pytest.raises(HomeAssistantError) as refused:
            await observer.async_use_next_calendar_event()
        assert refused.value.translation_key == "calendar_no_event"
        hass.services.async_call.return_value = None
        with pytest.raises(HomeAssistantError) as refused:
            await observer.async_use_next_calendar_event()
        assert refused.value.translation_key == "calendar_reply_invalid"
        hass.services.async_call.return_value = {"calendar.trips": {"events": [
            {"start": "2026-11-07Tbad", "summary": "Departure"},
            {"start": "2026-11-07T07:00:00", "summary": "Departure"},
            {"start": (NOW + timedelta(days=15)).isoformat(), "summary": "Departure"},
            "not an event",
        ]}}
        with pytest.raises(HomeAssistantError) as refused:
            await observer.async_use_next_calendar_event()
        assert refused.value.translation_key == "calendar_no_event"

    asyncio.run(run())


def test_departure_controls_show_the_services_time_and_delegate_actions() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        timed = SimpleNamespace(
            async_set_deadline=AsyncMock(), async_use_next_calendar_event=AsyncMock(),
        )
        runtime = entry.runtime_data
        runtime.timed_goal = timed
        datetimes: dict[str, object] = {}
        await setup_datetime(hass, entry, lambda items: datetimes.update(collect(items)))
        departure = datetimes["timed_departure"]
        # The entity shows the service's one time, whoever set it.
        soon = dt_util.utcnow() + timedelta(hours=8)
        await runtime.control.async_set_target_time(soon)
        assert departure.native_value == soon
        assert departure.available
        await departure.async_set_value(NOW + timedelta(hours=9))
        timed.async_set_deadline.assert_awaited_once_with(NOW + timedelta(hours=9))
        await DEVICE_ACTIONS["use_calendar_departure"](runtime)
        timed.async_use_next_calendar_event.assert_awaited_once()
        await departure.async_clear_value()
        timed.async_set_deadline.assert_any_await(None)
        # Without a selected car the time goes to the service directly.
        runtime.timed_goal = None
        await runtime.control.async_set_target_time(None)
        assert departure.native_value is None
        with pytest.raises(ServiceValidationError):
            await DEVICE_ACTIONS["use_calendar_departure"](runtime)
        later = dt_util.utcnow() + timedelta(hours=10)
        await departure.async_set_value(later)
        assert departure.native_value == later
        await runtime.coordinator.async_refresh()
        assert runtime.control.departure.trip.deadline == later
        await departure.async_clear_value()
        assert departure.native_value is None
        assert timed.async_set_deadline.await_count == 2
        await runtime.client.async_close()

    asyncio.run(run())


def test_the_selected_car_remembers_its_detected_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        remembered: list[tuple[str, int]] = []

        class Memory:
            def __init__(self, *_args) -> None:
                pass

            async def async_phases(self, profile: str) -> int | None:
                return 2 if profile == "car" else None

            async def async_remember(self, profile: str, phases: int) -> None:
                remembered.append((profile, phases))

        monkeypatch.setattr(observer_module, "VehiclePhaseMemory", Memory)
        observer, control, _hass = setup_observer(monkeypatch)
        observer._options["vehicle_profile_id"] = "car"
        monkeypatch.setattr(observer_module, "async_track_state_change_event", lambda *_args: lambda: None)
        monkeypatch.setattr(observer_module, "async_track_time_interval", lambda *_args: lambda: None)
        await observer.async_start()
        await observer.async_stop()
        assert observer._known_phases == 2
        control.data.values["detected_ev_phases"] = 0
        assert observer._phases() == 2
        control.data.values["detected_ev_phases"] = 1
        control.data.values["evse_state"] = EvseState.READY_TO_CHARGE
        # A reading without current may still be the previous car's.
        assert observer._phases() == 1
        await asyncio.sleep(0)
        assert remembered == []
        control.data.values["evse_state"] = EvseState.CHARGING
        assert observer._phases() == 1
        assert observer._phases() == 1
        await asyncio.sleep(0)
        assert remembered == [("car", 1)]
        observer._options.pop("vehicle_profile_id")
        control.data.values["detected_ev_phases"] = 3
        assert observer._phases() == 3
        assert remembered == [("car", 1)]

    asyncio.run(run())
