"""Exercise trip completion, concurrent edits and vehicle safety at runtime."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.timed_goal import TimedGoal
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


def test_no_vehicle_or_unknown_presence_never_changes_current(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        for evse, cp in ((EvseState.IDLE, CpState.A1), (EvseState.ERROR, CpState.E)):
            control.data.values.update({"evse_state": evse, "cp_state": cp})
            await observer._evaluate()
            assert observer.status == "waiting_for_device"
        assert control.commands == []

    asyncio.run(run())


def test_vehicle_cap_completion_cannot_rearm_from_unchanged_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        future = NOW + timedelta(hours=8)
        observer._entry.options["timed_departure_entity"] = "sensor.departure"
        hass.states["sensor.departure"] = state("sensor.departure", future.isoformat())
        goal = SocGoal()
        goal.configure_source("sensor.soc|")
        goal.set_target(80, soc=20)
        goal.observe(60, maximum=60)
        control.soc_goal = goal
        await observer._evaluate()
        assert observer.status == "reached"
        assert observer.goal.deadline == future
        goal.set_target(60, soc=20)
        control.is_paused = True
        await observer._evaluate()
        assert observer.status == "reached"
        assert control.commands == []
        # Keep the timestamp for preconditioning, then consume it once the
        # selected departure has passed without relabelling success as late.
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: future + timedelta(seconds=1))
        await observer._evaluate()
        assert observer.goal.deadline is None
        assert observer.goal.finished == "reached"
        assert control.commands == []

    asyncio.run(run())


def test_live_soc_at_goal_consumes_trip_without_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        hass.states["sensor.soc"] = state("sensor.soc", "80", unit_of_measurement="%")
        await observer._evaluate()
        assert observer.status == "reached"
        assert observer.goal.deadline == NOW + timedelta(hours=8)
        assert control.commands == []

    asyncio.run(run())


@pytest.mark.parametrize("stop", ["meter_unavailable", "meter_reset", "pending_soc"])
def test_safety_stop_retains_trip_and_recovery_resumes_pacing(monkeypatch: pytest.MonkeyPatch, stop: str) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        observer.goal.set(deadline)
        control.energy_goal.set_target(10, 100)
        await observer._evaluate()
        assert control.commands == [("current", 9.7)]
        original = observer.goal
        if stop != "pending_soc":
            control.energy_goal.observe(None if stop == "meter_unavailable" else 99)
            assert control.energy_goal.status == stop
        # The composite stop can also fence a start while a SoC observation
        # waits for its controller lock; it is not itself proof of completion.
        control.energy_goal_stop_requested = True
        control.is_paused = True
        control.data.values["charging_current_ems"] = 1.0
        await observer._evaluate()
        await observer._evaluate()
        assert observer.status == "waiting_for_device"
        assert observer.goal is original
        assert observer.goal.deadline == deadline
        assert observer.goal.finished is None
        assert control.commands == [("current", 9.7)]
        assert observer._store.saved == []
        if stop == "meter_unavailable":
            control.energy_goal.observe(101)
            assert control.energy_goal.status == "active"
        elif stop == "meter_reset":
            # Meter-reset safety remains latched until the energy goal is
            # explicitly cleared; the independent departure is still usable.
            control.energy_goal.set_target(0)
        control.energy_goal_stop_requested = False
        monkeypatch.setattr(observer_module.time, "monotonic", lambda: 301.0)
        await observer._evaluate()
        assert observer.status == "paced"
        assert observer.goal is original
        assert control.commands == [("current", 9.7), ("current", 9.7)]

    asyncio.run(run())


def test_reached_energy_ceiling_finishes_trip_without_resuming(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        observer.goal.set(deadline)
        control.energy_goal.set_target(10, 100)
        control.energy_goal.observe(110)
        control.energy_goal_stop_requested = True
        await observer._evaluate()
        assert observer.status == "reached"
        assert observer.goal.finished == "reached"
        assert observer.goal.deadline == deadline
        assert control.commands == []

    asyncio.run(run())


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
        await request
        assert observer.goal.deadline == NOW + timedelta(hours=2)
        assert len(observer._store.saved) == 1

    asyncio.run(run())


def test_deadline_saves_serialize_and_stale_commands_are_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        started, finish = asyncio.Event(), asyncio.Event()
        save = observer._store.async_save

        async def slow_save(goal: TimedGoal) -> None:
            if not started.is_set():
                started.set()
                await finish.wait()
            await save(goal)

        observer._store.async_save = slow_save
        first = asyncio.create_task(observer.async_set_deadline(NOW + timedelta(hours=1)))
        await started.wait()
        second = asyncio.create_task(observer.async_set_deadline(NOW + timedelta(hours=2)))
        finish.set()
        await asyncio.gather(first, second)
        assert observer.goal.deadline == NOW + timedelta(hours=2)
        assert observer._store.saved[-1] == observer.goal.snapshot()
        old = observer.goal
        await observer.async_set_deadline(None)
        await observer._write(16, old)
        await observer._finish("reached", old)
        assert control.commands == []
        assert observer.goal.finished is None
        observer._stopping = True
        await observer.async_set_deadline(NOW + timedelta(hours=3))
        assert observer.goal.deadline is None

    asyncio.run(run())


def test_store_failure_preserves_current_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        original = observer.goal
        observer._store.async_save = AsyncMock(side_effect=OSError("disk failed"))
        with pytest.raises(OSError, match="disk failed"):
            await observer.async_set_deadline(NOW + timedelta(hours=9))
        assert observer.goal is original

    asyncio.run(run())


def test_detected_vehicle_phases_bound_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        control.data.values["detected_ev_phases"] = 2
        await observer._evaluate()
        assert control.commands == [("current", 14.5)]
        control.data.values["detected_ev_phases"] = 1
        assert observer._phases() == 1
        control.data.values["switched_phases"] = 1
        control.data.values["detected_ev_phases"] = 3
        assert observer._phases() == 1

    asyncio.run(run())


def test_unknown_pause_leaves_charging_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        control.is_paused = None
        await observer._evaluate()
        assert control.commands == []

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
