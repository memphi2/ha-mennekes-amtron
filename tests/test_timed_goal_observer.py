"""Exercise trip completion, concurrent edits and vehicle safety at runtime."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import atomicwrites
import pytest

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.device.departure_plan import DepartureState
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
    StorageStateUnknownError,
)
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.goal_store import (
    DepartureIntent,
    TimedGoalStore,
)
from custom_components.mennekes_amtron_rtu.timed_goal_observer import departure_time
from tests.ha_fakes import FakeHass
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
        await request
        assert observer.goal.deadline == NOW + timedelta(hours=2)
        assert len(observer._store.saved) == 1

    asyncio.run(run())


def test_deadline_saves_serialize_and_a_stopped_observer_ignores_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, _hass = setup_observer(monkeypatch)
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
        await observer.async_set_deadline(None)
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


@pytest.mark.parametrize("operation", ["replace", "clear"])
def test_cancelled_save_keeps_live_and_durable_trip_consistent(monkeypatch, operation):
    async def run():
        observer, _control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        original = observer.goal
        started, finish = asyncio.Event(), asyncio.Event()
        save = observer._store.async_save

        async def slow_save(goal):
            started.set()
            await finish.wait()
            await save(goal)

        observer._store.async_save = slow_save
        deadline = None if operation == "clear" else NOW + timedelta(hours=9)
        task = asyncio.create_task(observer.async_set_deadline(deadline))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observer._store.saved[-1] == observer.goal.snapshot()
        assert observer.goal is not original
        assert observer.goal.deadline == deadline

    asyncio.run(run())


def test_cancelled_failed_save_preserves_live_trip(monkeypatch):
    async def run():
        observer, _control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        original = observer.goal
        started, finish = asyncio.Event(), asyncio.Event()

        async def failed_save(_goal):
            started.set()
            await finish.wait()
            raise OSError("disk failed")

        observer._store.async_save = failed_save
        task = asyncio.create_task(observer.async_set_deadline(None))
        await started.wait()
        task.cancel()
        finish.set()
        with pytest.raises(OSError, match="disk failed"):
            await task
        assert observer.goal is original

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["file_sync", "directory_sync"])
def test_failed_trip_save_matches_visible_state_and_reports_failure(tmp_path, monkeypatch, failure):
    async def run():
        observer, _control, _hass = setup_observer(monkeypatch)
        storage_hass = FakeHass(str(tmp_path))
        storage_hass.async_add_executor_job = asyncio.to_thread
        observer._store = TimedGoalStore(storage_hass, "entry", "SERIAL")
        observer.goal.set(NOW + timedelta(hours=8))
        await observer._store.async_save(DepartureIntent(observer.goal))
        original = observer.goal.snapshot()
        deadline = NOW + timedelta(hours=9)

        def fail(*_args):
            raise OSError("injected fsync failure")

        with monkeypatch.context() as scope:
            scope.setattr(atomicwrites, "_proper_fsync" if failure == "file_sync" else "_sync_directory", fail)
            expected = OSError if failure == "file_sync" else StorageCommitUncertainError
            with pytest.raises(expected):
                await observer.async_set_deadline(deadline)
        visible = await TimedGoalStore(storage_hass, "entry", "SERIAL").async_load()
        assert observer.goal.snapshot() == visible.trip.snapshot()
        assert (observer.goal.deadline == deadline) is (failure == "directory_sync")
        if failure == "file_sync":
            assert observer.goal.snapshot() == original
        else:
            assert observer._save_dirty
            writes = []
            write = observer._store._write

            def record_write(payload):
                writes.append(payload)
                write(payload)

            monkeypatch.setattr(observer._store, "_write", record_write)
            await observer.async_set_deadline(deadline)
            assert not observer._save_dirty
            assert len(writes) == 1

    asyncio.run(run())


def test_unknown_trip_storage_blocks_commands_until_cancelled_retry_succeeds(tmp_path, monkeypatch):
    async def run():
        observer, control, _hass = setup_observer(monkeypatch)
        storage_hass = FakeHass(str(tmp_path))
        storage_hass.async_add_executor_job = asyncio.to_thread
        observer._store = TimedGoalStore(storage_hass, "entry", "SERIAL")
        deadline = NOW + timedelta(hours=8)

        def fail(*_args):
            raise OSError("injected failure")

        with monkeypatch.context() as scope:
            scope.setattr(atomicwrites, "_sync_directory", fail)
            scope.setattr(observer._store, "_validate_file", fail)
            with pytest.raises(StorageStateUnknownError):
                await observer.async_set_deadline(deadline)
        assert observer._save_dirty
        assert observer._save_unknown
        # The service would plan the trip; an unknown save hands nothing over.
        control.departure = DepartureState(TimedGoal(), "disabled", None, None, None, None, False)
        await observer._evaluate()
        assert observer.status == "error"
        assert control.trips == []

        started, release = asyncio.Event(), asyncio.Event()
        save = observer._store.async_save

        async def delayed_save(goal):
            started.set()
            await release.wait()
            await save(goal)

        observer._store.async_save = delayed_save
        task = asyncio.create_task(observer.async_set_deadline(deadline))
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not observer._save_dirty
        assert not observer._save_unknown
        assert (await observer._store.async_load()).trip.snapshot() == observer.goal.snapshot()
        await observer._evaluate()
        assert control.trips == [(deadline, None, False)]

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
