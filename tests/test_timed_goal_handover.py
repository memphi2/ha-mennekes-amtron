"""With a planning service, Home Assistant hands the trip over and shows its state."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError

from custom_components.mennekes_amtron_rtu.device.departure_plan import (
    DepartureForecast,
    DepartureInputs,
    DepartureState,
)
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_timed_goal import NOW, setup_observer

SCOPE = "a" * 64
CAR = "profile:bmw:sensor.soc|"
REPORTED = NOW - timedelta(minutes=3)
FORECAST = {
    "finish_at": (NOW + timedelta(hours=8)).isoformat(), "level_at_departure": 74,
    "efficiency": 0.9, "full_power_kw": None,
}


def plan(trip: TimedGoal | None = None, **changes) -> dict:
    return {
        "trip": (trip or TimedGoal()).snapshot(), "status": "disabled", "planned_start": None,
        "estimated_soc": None, "inputs": None, "scope": SCOPE, "confirmed": False, "forecast": None, "editor": "ha", "selection_vehicle": None, "edit_revision": "a" * 32,
        **changes,
    }


class Service:
    @staticmethod
    def guest_active():
        return False

    """The service side of the hand-over, as the remote control presents it."""

    read_only = False

    def __init__(self) -> None:
        self.state: dict | None = plan()
        self.trips: list[tuple] = []
        self.inputs: list[DepartureInputs] = []
        self.cancelled = 0
        self.fail: Exception | None = None

    @property
    def departure(self) -> DepartureState | None:
        return None if self.state is None else DepartureState.restore(self.state)

    async def async_set_departure_trip(self, deadline, source, *, withdraw=False, expected_selection=None):
        if self.fail is not None:
            raise self.fail
        self.trips.append((deadline, source, withdraw))
        trip = TimedGoal()
        trip.set(deadline, source)
        self.state = {**self.state, "trip": trip.snapshot()}

    async def async_set_departure_inputs(self, inputs):
        self.inputs.append(inputs)
        self.state = {**self.state, "inputs": inputs.digest}

    async def async_cancel_timed_current(self, _owner):
        self.cancelled += 1

    async def async_set_timed_current(self, *_args, **_kwargs):
        pytest.fail("Home Assistant planned although the service does")


def handover(monkeypatch, *, binding=None):
    observer, _control, hass = setup_observer(monkeypatch)
    service = Service()
    runtime = observer._runtime
    runtime.control = service
    runtime.vehicle_binding = binding
    runtime.vehicle_soc = SimpleNamespace(source=CAR, latest_soc_reading=(20.0, REPORTED), latest_soc=20.0)
    return observer, service, hass


def test_the_trip_and_car_are_handed_over_once_and_the_service_state_is_shown(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        await observer.async_set_deadline(deadline, source="manual")
        await observer._evaluate()
        assert service.trips == [(deadline, "manual", False)]
        assert service.inputs == [DepartureInputs(CAR, SCOPE, 80.0, None, 20.0, REPORTED)]
        # A changed minimum level is handed over as well.
        observer._options["timed_minimum_soc"] = 30
        await observer._evaluate()
        assert service.inputs[-1] == DepartureInputs(CAR, SCOPE, 80.0, None, 20.0, REPORTED, 30.0)
        start = NOW + timedelta(hours=5)
        forecast = {"finish_at": deadline.isoformat(), "level_at_departure": 80, "efficiency": 0.88, "full_power_kw": 10.6}
        service.state = {
            **service.state, "status": "paused_until_start", "planned_start": start.isoformat(),
            "forecast": forecast,
        }
        await observer._evaluate()
        assert (observer.status, observer.planned_start) == ("paused_until_start", start)
        assert observer.forecast == DepartureForecast(deadline, 80.0, 0.88, 10.6)
        # Nothing changed: nothing is sent again.
        assert (len(service.trips), len(service.inputs)) == (1, 2)
    asyncio.run(run())


def test_an_unconfirmed_car_is_handed_over_without_a_plug_in(monkeypatch):
    async def run():
        binding = SimpleNamespace(resting=True)
        observer, service, _hass = handover(monkeypatch, binding=binding)
        await observer._evaluate()
        assert service.inputs[-1].scope is None
        binding.resting = False
        await observer._evaluate()
        assert service.inputs[-1].scope == SCOPE
        # Without a selected car there is nothing to hand over.
        observer._runtime.vehicle_soc = None
        await observer._evaluate()
        assert len(service.inputs) == 2
    asyncio.run(run())


def test_the_service_decides_how_a_trip_ends(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        await observer.async_set_deadline(deadline, source="manual")
        await observer._evaluate()
        reached = TimedGoal.restore(service.state["trip"])
        reached.finish("reached")
        service.state = {**service.state, "trip": reached.snapshot(), "status": "reached"}
        await observer._evaluate()
        assert observer.goal.snapshot() == reached.snapshot()
        assert observer.status == "reached"
        # A higher target lets the service plan the same trip again.
        service.state = {**service.state, "trip": {**reached.snapshot(), "finished": None}, "status": "charging"}
        await observer._evaluate()
        assert observer.goal.finished is None
        assert len(service.trips) == 1
    asyncio.run(run())


def test_a_trip_the_service_finished_at_its_time_is_adopted_not_sent_again(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        observer.goal.set(NOW - timedelta(minutes=1), "manual")
        service.state = plan(status="late", trip=None)
        service.state["trip"] = {"deadline": None, "source": "manual", "finished": "late"}
        await observer._evaluate()
        assert observer.goal.snapshot() == {"deadline": None, "source": "manual", "finished": "late"}
        assert service.trips == []
    asyncio.run(run())


@pytest.mark.parametrize(("minutes", "adopted"), [(2, True), (10, False)])
def test_a_service_clock_slightly_ahead_still_ends_the_trip(monkeypatch, minutes, adopted):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        deadline = NOW + timedelta(minutes=minutes)
        observer.goal.set(deadline, "manual")
        ended = {"deadline": None, "source": "manual", "finished": "late"}
        service.state = {**plan(status="late"), "trip": ended}
        await observer._evaluate()
        # Further ahead than a clock differs, the service lost the trip instead.
        assert service.trips == ([] if adopted else [(deadline, "manual", False)])
        assert (observer.goal.snapshot() == ended) is adopted
    asyncio.run(run())


def test_a_service_that_lost_its_plan_gets_the_trip_again(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        observer.goal.set(deadline, "manual")
        await observer._evaluate()
        assert service.trips == [(deadline, "manual", False)]
        # A cleared trip is sent as well.
        await observer.async_set_deadline(None, source="manual")
        await observer._evaluate()
        assert service.trips[-1] == (None, "manual", False)
    asyncio.run(run())


def test_a_failed_hand_over_is_tried_again(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        await observer.async_set_deadline(NOW + timedelta(hours=8), source="manual")
        service.fail = HomeAssistantError("service_unavailable")
        with pytest.raises(HomeAssistantError):
            await observer._evaluate()
        assert observer._trip_dirty
        service.fail = None
        await observer._evaluate()
        assert len(service.trips) == 1
        assert not observer._trip_dirty
    asyncio.run(run())


def test_a_loading_service_is_waited_for(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        service.state = None
        await observer._evaluate()
        assert observer.status == "waiting_for_device"
        assert (service.trips, service.inputs) == ([], [])
    asyncio.run(run())


@pytest.mark.parametrize("withdraw", [True, False])
def test_only_another_car_withdraws_the_services_plan(monkeypatch, withdraw):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        observer.goal.set(NOW + timedelta(hours=8), "manual")
        await observer.async_stop(withdraw=withdraw)
        assert service.trips == ([(None, "manual", True)] if withdraw else [])
    asyncio.run(run())


def test_the_remote_control_validates_and_sends_the_plan(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                control = ServiceControl(
                    client, state=lambda: None, request_refresh=lambda: None, notify_pending=lambda: None,
                )
                payload = service.snapshot()
                client.apply_snapshot(payload)
                client._connected = True
                payload["control"]["departure"] = None
                client.apply_snapshot(payload)
                assert control.departure is None
                payload["control"]["departure"] = plan(estimated_soc=41.5, inputs="b" * 32, forecast=FORECAST)
                client.apply_snapshot(payload)
                assert control.departure.estimated_soc == 41.5
                assert control.departure.forecast.full_power_kw is None
                sent = []

                async def request(_method, _path, body):
                    sent.append(body)
                    return payload

                client.request = request
                deadline = NOW + timedelta(hours=8)
                await control.async_set_departure_trip(deadline, "manual", withdraw=True)
                given = DepartureInputs(CAR, SCOPE, 60.0, 3, 20.0, REPORTED)
                await control.async_set_departure_inputs(given)
                serial = client.identity.serial_number
                assert sent == [
                    {"command": "departure_trip", "origin": "home_assistant", "deadline": deadline.isoformat(), "source": "manual",
                     "withdraw": True, "expected_serial_number": serial},
                    {"command": "departure_inputs", "origin": "home_assistant", "value": given.snapshot(), "expected_serial_number": serial},
                ]
                for broken in (
                    {"departure": {}},
                    {"departure": plan(status="flying")}, {"departure": plan(scope="x")},
                    {"departure": plan(inputs="x")}, {"departure": plan(confirmed=1)},
                    {"departure": plan(estimated_soc=101)}, {"departure": plan(planned_start="soon")},
                    {"departure": plan(forecast={})},
                    {"departure": plan(forecast={**FORECAST, "efficiency": 1.2})},
                    {"departure": plan(forecast={**FORECAST, "efficiency": None})},
                    {"departure": plan(forecast={**FORECAST, "full_power_kw": True})},
                    {"departure": plan(forecast={**FORECAST, "full_power_kw": 0})},
                    {"departure": plan(forecast={**FORECAST, "finish_at": "soon"})},
                ):
                    damaged = {**payload, "control": {**payload["control"], **broken}}
                    with pytest.raises(ServiceConnectionError, match=r"control\.departure"):
                        client.apply_snapshot(damaged)
                # The service's summary is validated like its plan.
                assert control.operation is None
                summary = {
                    "controller": "departure_plan", "waiting": "until_planned_start",
                    "next": {"event": "plan_start", "at": deadline.isoformat()},
                    "target": {"kind": "level", "value": 80.0, "deadline": deadline.isoformat()},
                    "vehicle": "confirmed",
                }
                payload["control"]["operation"] = summary
                client.apply_snapshot(payload)
                assert (control.operation.controller, control.operation.next_at) == ("departure_plan", deadline)
                damaged = {**payload, "control": {**payload["control"], "operation": {**summary, "waiting": "x"}}}
                with pytest.raises(ServiceConnectionError, match=r"control\.operation"):
                    client.apply_snapshot(damaged)
                assert control.operation.waiting == "until_planned_start"
        finally:
            await service.stop()
    asyncio.run(run())


def test_new_app_departure_supersedes_an_unsent_ha_edit(monkeypatch):
    async def body():
        observer, service, _hass = handover(monkeypatch)
        await observer.async_set_deadline(NOW + timedelta(hours=5), source="manual")
        newer = TimedGoal()
        newer.set(NOW + timedelta(hours=7), "manual")
        service.state = plan(newer, editor="app", selection_vehicle=CAR, edit_revision="b" * 32)
        await observer._evaluate()
        assert observer.goal.deadline == newer.deadline
        assert not observer._trip_dirty
        assert service.trips == []
    asyncio.run(body())


def test_foreign_vehicle_app_choice_is_not_adopted(monkeypatch):
    async def body():
        observer, service, _hass = handover(monkeypatch)
        await observer.async_set_deadline(NOW + timedelta(hours=5), source="manual")
        newer = TimedGoal()
        newer.set(NOW + timedelta(hours=7), "manual")
        service.state = plan(newer, editor="app", selection_vehicle="another-car")
        await observer._evaluate()
        assert observer.goal.deadline == NOW + timedelta(hours=5)
        assert service.trips == [(observer.goal.deadline, "manual", False)]
    asyncio.run(body())


@pytest.mark.parametrize("completion", ["reached", "late"])
def test_automatic_completion_of_app_trip_does_not_discard_new_ha_choice(monkeypatch, completion):
    async def body():
        observer, service, _hass = handover(monkeypatch)
        old = TimedGoal()
        old.set(NOW + timedelta(hours=1), "manual")
        service.state = plan(old, editor="app", selection_vehicle=CAR)
        await observer._evaluate()
        new_deadline = NOW + timedelta(hours=8)
        await observer.async_set_deadline(new_deadline, source="manual")
        old.finish(completion)
        service.state["trip"] = old.snapshot()  # Lifecycle change, same edit revision.
        await observer._evaluate()
        assert observer.goal.deadline == new_deadline
        assert observer.goal.finished is None
        assert service.trips == [(new_deadline, "manual", False)]
        assert not observer._trip_dirty
    asyncio.run(body())


@pytest.mark.parametrize("newer_app", [False, True])
@pytest.mark.parametrize("clear", [False, True])
def test_pending_ha_edit_survives_reload(tmp_path, monkeypatch, newer_app, clear):
    from custom_components.mennekes_amtron_rtu import timed_goal_observer as module
    from custom_components.mennekes_amtron_rtu.goal_store import TimedGoalStore
    from tests.ha_fakes import FakeHass

    async def body():
        observer, service, _hass = handover(monkeypatch)
        storage = FakeHass(str(tmp_path))
        storage.async_add_executor_job = asyncio.to_thread
        observer._store = TimedGoalStore(storage, "entry", "SERIAL")
        old = TimedGoal()
        old.set(NOW + timedelta(hours=1), "manual")
        service.state = plan(old, editor="app", selection_vehicle=CAR)
        await observer._evaluate()
        deadline = None if clear else NOW + timedelta(hours=8)
        await observer.async_set_deadline(deadline, source="manual")
        service.fail = HomeAssistantError("service_unavailable")
        with pytest.raises(HomeAssistantError):
            await observer._evaluate()
        await observer.async_stop()
        if newer_app:
            service.state = plan(old, editor="app", selection_vehicle=CAR, edit_revision="b" * 32)
        service.fail = None
        restarted, _, _ = handover(monkeypatch)
        restarted._runtime.control = service
        restarted._store = TimedGoalStore(storage, "entry", "SERIAL")
        monkeypatch.setattr(module, "async_track_state_change_event", lambda *_args: lambda: None)
        monkeypatch.setattr(module, "async_track_time_interval", lambda *_args: lambda: None)
        await restarted.async_start()
        assert restarted._trip_dirty
        assert restarted._trip_base == {"edit_revision": "a" * 32}
        await restarted._evaluate()
        assert restarted.goal.deadline == (old.deadline if newer_app else deadline)
        assert service.trips == ([] if newer_app else [(deadline, "manual", False)])
        saved = await TimedGoalStore(storage, "entry", "SERIAL").async_load()
        assert not saved.pending
        assert saved.base_revision is None
        assert saved.trip.snapshot() == restarted.goal.snapshot()
        await restarted.async_stop()
    asyncio.run(body())


@pytest.mark.parametrize("raw", [{}, {"trip": {}, "pending": True, "base_revision": None},
    {"trip": TimedGoal().snapshot(), "pending": 1, "base_revision": None},
    {"trip": TimedGoal().snapshot(), "pending": True, "base_revision": "invalid"},
    {"trip": TimedGoal().snapshot(), "pending": False, "base_revision": "a" * 32}])
def test_invalid_departure_provenance_is_rejected(raw):
    from custom_components.mennekes_amtron_rtu.goal_store import DepartureIntent
    with pytest.raises(ValueError, match="invalid departure"):
        DepartureIntent.restore(raw)


def test_guest_mode_holds_vehicle_handover_but_keeps_explicit_time_editing(monkeypatch):
    from unittest.mock import AsyncMock
    async def run():
        observer, service, _hass = handover(monkeypatch)
        service.guest_active = lambda: True
        service.async_set_target_time = AsyncMock()
        service.state["status"] = "guest_mode"
        original = observer.goal.snapshot()
        await observer._follow_service()
        assert service.trips == []
        assert service.inputs == []
        assert observer.status == "guest_mode"
        deadline = NOW + timedelta(hours=1)
        await observer.async_set_deadline(deadline)
        service.async_set_target_time.assert_awaited_once_with(deadline)
        assert observer.goal.snapshot() == original
    asyncio.run(run())


def test_a_new_trip_hides_the_old_planned_start_until_the_service_plans_it(monkeypatch):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        deadline = NOW + timedelta(hours=8)
        await observer.async_set_deadline(deadline, source="manual")
        start = NOW + timedelta(hours=5)
        service.state = {**service.state, "status": "paused_until_start", "planned_start": start.isoformat()}
        await observer._evaluate()
        assert observer.planned_start == start
        # Another trip plans its own start instead of keeping the old one.
        await observer.async_set_deadline(deadline + timedelta(hours=1), source="manual")
        assert observer.planned_start is None
        later = NOW + timedelta(hours=6)
        service.state = {**service.state, "planned_start": later.isoformat()}
        await observer._evaluate()
        assert service.trips[-1] == (deadline + timedelta(hours=1), "manual", False)
        assert observer.planned_start == later
        # Without the service's plan nothing is shown as planned.
        service.state = None
        await observer._evaluate()
        assert (observer.status, observer.planned_start, observer.forecast) == ("waiting_for_device", None, None)
    asyncio.run(run())


def test_a_capacity_sensor_in_wh_is_handed_over_in_kwh(monkeypatch):
    async def run():
        observer, service, hass = handover(monkeypatch)
        observer._options.pop("timed_capacity_kwh")
        observer._options["timed_capacity_entity"] = "sensor.capacity"
        hass.states["sensor.capacity"] = SimpleNamespace(
            entity_id="sensor.capacity", state="64000", attributes={"unit_of_measurement": "Wh"},
        )
        await observer._evaluate()
        assert service.inputs[-1].capacity_kwh == 64.0
    asyncio.run(run())


@pytest.mark.parametrize("pending", [False, True])
def test_a_trip_replaced_while_the_app_edit_waits_is_kept(monkeypatch, pending):
    async def run():
        observer, service, _hass = handover(monkeypatch)
        if pending:
            # An unsent edit here, which a newer app edit would supersede.
            await observer.async_set_deadline(NOW + timedelta(hours=5), source="manual")
        newer = TimedGoal()
        newer.set(NOW + timedelta(hours=7), "manual")
        service.state = plan(newer, editor="app", selection_vehicle=CAR, edit_revision="b" * 32)
        replacement = TimedGoal()
        replacement.set(NOW + timedelta(hours=9), "manual")
        async with observer._goal_lock:
            following = asyncio.create_task(observer._follow_service())
            for _ in range(5):
                await asyncio.sleep(0)
            # Another edit takes the trip's place before the app's is adopted.
            observer.goal = replacement
        await following
        assert observer.goal is replacement
        assert observer._trip_dirty is pending
        assert service.trips == []
    asyncio.run(run())
