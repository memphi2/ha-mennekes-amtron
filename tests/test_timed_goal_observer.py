"""Exercise trip completion, concurrent edits and vehicle safety at runtime."""

import asyncio
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.timed_goal_observer import departure_time
from tests.test_timed_goal import NOW, setup_observer, state


def test_completed_trip_roundtrip_and_invalid_completion() -> None:
    goal = TimedGoal()
    goal.set(NOW + timedelta(hours=1), "external:next-trip")
    goal.finish("reached")
    assert goal.deadline == NOW + timedelta(hours=1)
    assert TimedGoal.restore(goal.snapshot()).snapshot() == goal.snapshot()
    goal.finish("reached", clear_deadline=True)
    assert goal.deadline is None
    assert goal.set(None, goal.source)
    assert goal.finished is None
    with pytest.raises(ValueError, match="completion"):
        goal.finish("unknown")
    for raw in (
        {"deadline": NOW.isoformat(), "source": None, "finished": "late"},
        {"deadline": None, "source": None, "finished": "unknown"},
        {"deadline": None, "source": None, "finished": []},
    ):
        with pytest.raises(ValueError, match="invalid departure"):
            TimedGoal.restore(raw)


@pytest.mark.parametrize("attributes", [
    {"has_date": False}, {"has_time": False}, {"timestamp": True},
])
def test_partial_or_boolean_input_datetime_is_not_a_departure(attributes: dict) -> None:
    assert departure_time(state(
        "input_datetime.trip", "06:00:00", **{"timestamp": NOW.timestamp(), **attributes},
    )) is None


def test_manual_selection_wins_until_external_departure_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        future = NOW + timedelta(hours=8)
        observer._entry.options["timed_departure_entity"] = "sensor.departure"
        hass.states["sensor.departure"] = state("sensor.departure", future.isoformat())
        await observer.async_set_deadline(future + timedelta(hours=1))
        await observer._evaluate()
        assert observer.goal.deadline == future + timedelta(hours=1)
        hass.states["sensor.departure"] = state("sensor.departure", (future + timedelta(hours=2)).isoformat())
        await observer._evaluate()
        assert observer.goal.deadline == future + timedelta(hours=2)

    asyncio.run(run())


def test_slow_calendar_lookup_cannot_overwrite_a_new_manual_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        observer._entry.options.update({
            "timed_calendar_entity": "calendar.trip", "timed_calendar_title": "Trip",
        })
        started, finish = asyncio.Event(), asyncio.Event()

        async def fetch(*_args: object, **_kwargs: object) -> dict:
            started.set()
            await finish.wait()
            return {"calendar.trip": {"events": [{
                "start": (NOW + timedelta(hours=1)).isoformat(), "summary": "Trip",
            }]}}

        hass.services.async_call = fetch
        request = asyncio.create_task(observer.async_use_next_calendar_event())
        await started.wait()
        await observer.async_set_deadline(NOW + timedelta(hours=2))
        finish.set()
        # The service refuses the calendar's time: it was asked for another trip.
        with pytest.raises(HomeAssistantError, match="soc_goal_source_changed"):
            await request
        assert observer.goal.deadline == NOW + timedelta(hours=2)
        assert len(_control.trips) == 1

    asyncio.run(run())


def test_a_stopped_observer_ignores_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        await observer.async_set_deadline(NOW + timedelta(hours=2))
        observer._stopping = True
        await observer.async_set_deadline(NOW + timedelta(hours=3))
        assert observer.goal.deadline == NOW + timedelta(hours=2)
        assert len(control.trips) == 1

    asyncio.run(run())


def test_detected_vehicle_phases_bound_the_planning_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        assert observer._phases() == 3
        control.data.values["detected_ev_phases"] = 2
        assert observer._phases() == 2
        control.data.values["detected_ev_phases"] = 1
        assert observer._phases() == 1
        control.data.values["switched_phases"] = 1
        control.data.values["detected_ev_phases"] = 3
        assert observer._phases() == 1

    asyncio.run(run())


def test_missing_external_timestamp_does_not_replace_manual_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, _hass = setup_observer(monkeypatch)
        observer._entry.options["timed_departure_entity"] = "sensor.missing"
        deadline = NOW + timedelta(hours=8)
        await observer.async_set_deadline(deadline)
        await observer._evaluate()
        assert observer.goal.deadline == deadline

    asyncio.run(run())


def test_observer_coalesces_events_and_cancels_inflight_work(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, _hass = setup_observer(monkeypatch)
        observer._entry.options.clear()
        unsubscribed: list[bool] = []
        monkeypatch.setattr(observer_module, "async_track_time_interval",
                            lambda *_args: lambda: unsubscribed.append(True))
        observer.request_evaluate = observer_module.TimedGoalObserver.request_evaluate.__get__(observer)
        await observer.async_start()
        await observer._task
        waiting = asyncio.Event()
        calls = 0

        async def evaluate() -> None:
            nonlocal calls
            calls += 1
            waiting.set()
            await asyncio.Event().wait()

        observer._evaluate = evaluate
        observer._source_changed(None)
        await waiting.wait()
        task = observer._task
        observer._timer(NOW)
        assert observer._task is task
        await observer.async_stop()
        assert task.cancelled()
        assert calls == 1
        assert unsubscribed == [True]
        observer._pending = True
        await observer._run()
        # Stopping an observer that never started is harmless.
        unused, _control, _hass = setup_observer(monkeypatch)
        await unused.async_stop()

    asyncio.run(run())
