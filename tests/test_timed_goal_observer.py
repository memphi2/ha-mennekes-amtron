"""Exercise trip completion, concurrent edits and vehicle safety at runtime."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import atomicwrites
import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

import custom_components.mennekes_amtron_rtu.timed_goal_observer as observer_module
from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.data import WallboxData, WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
    StorageStateUnknownError,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from custom_components.mennekes_amtron_rtu.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.timed_goal_observer import departure_time
from tests.ha_fakes import FakeHass
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, current_writes, service_fixture
from tests.test_timed_goal import NOW, setup_observer, state


@pytest.mark.parametrize("detected", ["missing", None, 0])
def test_unknown_vehicle_phases_do_not_postpone_a_reachable_one_phase_trip(monkeypatch, detected):
    async def run():
        observer, control, _hass = setup_observer(monkeypatch)
        control.data.values.update(evse_state=EvseState.EV_CONNECTED, cp_state=CpState.B1)
        if detected == "missing":
            control.data.values.pop("detected_ev_phases")
        else:
            control.data.values["detected_ev_phases"] = detected
        observer.goal.set(NOW + timedelta(hours=16))

        # 48 kWh is reachable on one phase at 14.5 A. Assuming three phases
        # while detection is uninitialised previously inserted a three-hour
        # pause, leaving under 43 kWh available even at the eventual 16 A max.
        await observer._evaluate()
        assert observer.status == "paced"
        assert control.commands == [("current", 14.5)]
        assert control.commands[0][1] * 230 * 0.9 * 16 / 1000 >= 48

        # Confirming one phase does not suddenly turn the plan unreachable.
        control.data.values["detected_ev_phases"] = 1
        await observer._evaluate()
        assert observer.status == "paced"
        assert control.commands == [("current", 14.5)]

    asyncio.run(run())


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
        # The car reports the same charge level the target saw.
        hass.states["sensor.soc"] = state("sensor.soc", "60", unit_of_measurement="%")
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


def test_a_raised_target_plans_a_reached_trip_again(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        observer.goal.set(deadline)
        hass.states["sensor.soc"] = state("sensor.soc", "80", unit_of_measurement="%")
        await observer._evaluate()
        assert observer.goal.finished == "reached"
        assert observer.planned_start is None
        # A charge level falling before departure keeps the trip finished.
        hass.states["sensor.soc"] = state("sensor.soc", "78", unit_of_measurement="%")
        await observer._evaluate()
        assert observer.status == "reached"
        hass.states["sensor.soc"] = state("sensor.soc", "80", unit_of_measurement="%")
        control.soc_goal.target_percent = 90.0
        await observer._evaluate()
        assert observer.goal.finished is None
        assert observer.goal.deadline == deadline
        await observer._evaluate()
        # 8 kWh in eight hours fits at the minimum current, so it waits.
        assert observer.status == "paused_until_start"
        assert NOW < observer.planned_start < deadline
        assert control.commands == [("pause", None)]

    asyncio.run(run())


def test_a_reached_kwh_target_does_not_reopen_the_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        hass.states["sensor.soc"] = state("sensor.soc", "80", unit_of_measurement="%")
        await observer._evaluate()
        control.soc_goal.target_percent = 90.0
        control.energy_goal = SimpleNamespace(status="reached")
        await observer._evaluate()
        assert observer.goal.finished == "reached"

    asyncio.run(run())


def test_planned_start_follows_the_plan_and_clears_without_one(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, _control, hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=2))
        await observer._evaluate()
        # 48 kWh in two hours charges at once; the start is now.
        assert observer.status in ("paced", "unreachable")
        assert observer.planned_start == NOW
        later = NOW + timedelta(minutes=5)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: later)
        await observer._evaluate()
        assert observer.planned_start == NOW
        # Another trip plans its own start instead of keeping the old one.
        await observer.async_set_deadline(later + timedelta(hours=3))
        assert observer.planned_start is None
        await observer._evaluate()
        assert observer.planned_start is not None
        del hass.states["sensor.soc"]
        await observer._evaluate()
        assert observer.status == "waiting_for_soc"
        assert observer.planned_start is None

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


@pytest.mark.parametrize("operation", ["replace", "clear", "finish"])
def test_cancelled_save_keeps_live_and_durable_trip_consistent(monkeypatch, operation):
    async def run():
        observer, control, _hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        original = observer.goal
        started, finish = asyncio.Event(), asyncio.Event()
        save = observer._store.async_save

        async def slow_save(goal):
            started.set()
            await finish.wait()
            await save(goal)

        observer._store.async_save = slow_save
        if operation == "finish":
            action = observer._finish("reached", original)
        else:
            deadline = None if operation == "clear" else NOW + timedelta(hours=9)
            action = observer.async_set_deadline(deadline)
        task = asyncio.create_task(action)
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observer._store.saved[-1] == observer.goal.snapshot()
        if operation == "finish":
            assert observer.goal.finished == "reached"
        else:
            assert observer.goal.deadline == deadline
        await observer._evaluate()
        if operation != "replace":
            assert control.commands == []

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
        await observer._store.async_save(observer.goal)
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
        assert observer.goal.snapshot() == visible.snapshot()
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
        await observer._evaluate()
        assert observer.status == "error"
        assert not control.commands

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
        assert (await observer._store.async_load()).snapshot() == observer.goal.snapshot()
        await observer._evaluate()
        assert control.commands

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


@pytest.mark.parametrize("manual", [False, True])
def test_disabling_soc_target_cancels_only_owned_real_queue(monkeypatch: pytest.MonkeyPatch, manual: bool) -> None:
    async def run() -> None:
        observer, _fake_control, _hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        harness = Harness()
        control = await harness.connect()
        release = asyncio.Event()
        control._sleep = lambda _delay: release.wait()
        control.soc_goal.configure_source("sensor.soc|")
        control.soc_goal.set_target(80, soc=20)
        observer._runtime.control = control
        observer.goal.set(now + timedelta(hours=8))
        try:
            await control.async_pause_charging()
            await observer._evaluate()
            assert control.pending_action == "set_current"
            assert control._pending.owner == observer._command_owner
            task = control._flush_task
            if manual:
                await control.async_set_charging_current(12)
            control.soc_goal.set_target(0)
            await observer._evaluate()
            assert observer.status == "waiting_for_target"
            assert observer.goal.finished is None
            assert (control.pending_action is not None) is manual
            harness.clock[0] += 301
            release.set()
            await asyncio.wait_for(task, 2)
            assert current_writes(harness.transport) == ([1, 12] if manual else [1])
        finally:
            release.set()
            await observer.async_stop()
            await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("manual", [False, True])
def test_disabling_target_cancels_service_queue_without_removing_manual_work(tmp_path, monkeypatch: pytest.MonkeyPatch, manual: bool) -> None:
    async def run() -> None:
        observer, _fake_control, _hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        service, transport, clock = await service_fixture(tmp_path)
        release = asyncio.Event()
        try:
            await service.heartbeat.async_stop()
            service.control._sleep = lambda _delay: release.wait()
            await service.control.async_configure_soc("sensor.soc|")
            await service.control.async_set_soc_goal(80, source="sensor.soc|", soc=20)
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                    state=lambda: WallboxData(), request_refresh=None,
                )
                observer._runtime.control = control
                observer.goal.set(now + timedelta(hours=8))
                await control.async_pause_charging()
                await observer._evaluate()
                assert service.control.pending_action == "set_current"
                task = service.control._flush_task
                if manual:
                    await control.async_set_charging_current(12)
                await control.async_set_soc_goal(0, source="sensor.soc|")
                await observer._evaluate()
                assert observer.status == "waiting_for_target"
                assert observer.goal.finished is None
                assert (service.control.pending_action is not None) is manual
                clock[0] += 301
                release.set()
                await asyncio.wait_for(task, 2)
                assert current_writes(transport) == ([1, 12] if manual else [1])
                await observer.async_stop()
                await remote.async_close()
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("missing", "status"), [
    ("capacity", "waiting_for_capacity"), ("soc", "waiting_for_soc"),
    ("device", "waiting_for_device"), ("read_only", "read_only"),
    ("vehicle", "waiting_for_device"), ("safety_stop", "waiting_for_device"),
])
def test_missing_planning_input_cancels_future_work_without_stopping_charge(monkeypatch: pytest.MonkeyPatch, missing: str, status: str) -> None:
    async def run() -> None:
        observer, control, hass = setup_observer(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8))
        await observer._evaluate()
        control.async_cancel_timed_current = AsyncMock()
        original = observer.goal
        if missing == "capacity":
            observer._entry.options.pop("timed_capacity_kwh")
        elif missing == "soc":
            hass.states.pop("sensor.soc")
        elif missing == "device":
            observer._runtime.coordinator.last_update_success = False
        elif missing == "read_only":
            control.read_only = True
        elif missing == "vehicle":
            control.data.values.update({"evse_state": EvseState.IDLE, "cp_state": CpState.A1})
        else:
            control.energy_goal_stop_requested = True
        await observer._evaluate()
        assert observer.status == status
        control.async_cancel_timed_current.assert_awaited_once_with(observer._command_owner)
        assert observer.goal is original
        assert control.commands == [("current", 9.7)]

    asyncio.run(run())


@pytest.mark.parametrize("pending_owner", ["same_entry", "manual", "other_entry"])
def test_reloaded_observer_reclaims_service_queue_after_offline_unload(tmp_path, monkeypatch: pytest.MonkeyPatch, pending_owner: str) -> None:
    async def run() -> None:
        observer, _fake_control, hass = setup_observer(monkeypatch)
        now = datetime.now(UTC)
        monkeypatch.setattr(observer_module.dt_util, "utcnow", lambda: now)
        monkeypatch.setattr(observer_module, "async_track_state_change_event", lambda *_args: lambda: None)
        monkeypatch.setattr(observer_module, "async_track_time_interval", lambda *_args: lambda: None)
        service, transport, clock = await service_fixture(tmp_path)
        release = asyncio.Event()
        try:
            await service.heartbeat.async_stop()
            service.control._sleep = lambda _delay: release.wait()
            await service.control.async_configure_soc("sensor.soc|")
            await service.control.async_set_soc_goal(80, source="sensor.soc|", soc=20)
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                endpoint = str(server.make_url("/"))
                remote = RemoteClient(session, endpoint, TOKEN)
                await remote.async_connect()
                control = RemoteControl(
                    remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                    state=lambda: WallboxData(), request_refresh=None,
                )
                observer._runtime.control = control
                observer.goal.set(now + timedelta(hours=8))
                saved = observer.goal.snapshot()
                await control.async_pause_charging()
                await observer._evaluate()
                assert service.control._pending.owner == observer._command_owner
                if pending_owner == "manual":
                    await control.async_set_charging_current(12)
                elif pending_owner == "other_entry":
                    await control.async_cancel_timed_current(observer._command_owner)
                    await control.async_set_timed_current(
                        12, deadline=observer.goal.deadline, owner="timed:other_entry",
                    )
                task = service.control._flush_task
                await remote.async_close()
                await observer.async_stop()
                assert service.control.pending_action == "set_current"

                fresh = RemoteClient(session, endpoint, TOKEN)
                await fresh.async_connect()
                observer._runtime.control = RemoteControl(
                    fresh, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                    state=lambda: WallboxData(), request_refresh=None,
                )
                reloaded = observer_module.TimedGoalObserver(hass, observer._entry)
                reloaded.request_evaluate = lambda: None
                reloaded._store.async_load = AsyncMock(return_value=TimedGoal.restore(saved))
                await reloaded.async_start()
                assert reloaded._command_owner == observer._command_owner
                await reloaded.async_set_deadline(None)
                assert reloaded.goal.deadline is None
                assert (service.control.pending_action is not None) is (pending_owner != "same_entry")
                clock[0] += 301
                release.set()
                await asyncio.wait_for(task, 2)
                assert current_writes(transport) == ([1] if pending_owner == "same_entry" else [1, 12])
                await reloaded.async_stop()
                await fresh.async_close()
        finally:
            release.set()
            await service.stop()

    asyncio.run(run())


def test_failed_queue_reclaim_cannot_start_new_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        observer, control, _hass = setup_observer(monkeypatch)
        control.async_cancel_timed_current = AsyncMock(side_effect=observer_module.HomeAssistantError("service offline"))
        observer._store.async_load = AsyncMock()
        with pytest.raises(observer_module.HomeAssistantError, match="service offline"):
            await observer.async_start()
        observer._store.async_load.assert_not_awaited()
        assert observer._task is None
        assert observer._unsubs == []
        assert observer._command_submitted
        await observer.async_stop()

    asyncio.run(run())
