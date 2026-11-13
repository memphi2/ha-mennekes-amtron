"""With a planning service, Home Assistant hands the trip over and shows its state."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError

from custom_components.mennekes_amtron_rtu.control import AmtronControl
from custom_components.mennekes_amtron_rtu.device.data import WriteDiagnostics
from custom_components.mennekes_amtron_rtu.device.departure_plan import (
    DepartureForecast,
    DepartureInputs,
    DepartureState,
)
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.timed_goal import TimedGoal
from custom_components.mennekes_amtron_rtu.remote import (
    ControlOptions,
    RemoteClient,
    RemoteControl,
)
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
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
        "estimated_soc": None, "inputs": None, "scope": SCOPE, "confirmed": False, "forecast": None,
        **changes,
    }


class Service:
    """The service side of the hand-over, as the remote control presents it."""

    supports_departure_plan = True
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

    async def async_set_departure_trip(self, deadline, source, *, withdraw=False):
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
        binding = SimpleNamespace(resting=True, detected=False)
        observer, service, _hass = handover(monkeypatch, binding=binding)
        await observer._evaluate()
        assert (service.inputs[-1].scope, service.inputs[-1].detected) == (None, False)
        binding.resting, binding.detected = False, True
        await observer._evaluate()
        assert (service.inputs[-1].scope, service.inputs[-1].detected) == (SCOPE, True)
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


def test_the_direct_backend_never_plans_in_a_service():
    async def run():
        control = AmtronControl.__new__(AmtronControl)
        assert control.supports_departure_plan is False
        assert control.departure is None
        with pytest.raises(DeviceError, match="service_unavailable"):
            await control.async_set_departure_trip(None, None)
        with pytest.raises(DeviceError, match="service_unavailable"):
            await control.async_set_departure_inputs(DepartureInputs(CAR))
    asyncio.run(run())


def test_the_remote_control_validates_and_sends_the_plan(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = RemoteClient(session, "http://service:8765", TOKEN)
                control = RemoteControl(
                    client, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                    state=lambda: None, request_refresh=lambda: None, notify_pending=lambda: None,
                )
                payload = service.snapshot()
                client.apply_snapshot(payload)
                client._connected = True
                assert (control.supports_departure_plan, control.departure) == (False, None)
                with pytest.raises(HomeAssistantError):
                    await control.async_set_departure_trip(None, None)
                payload["control"]["supports_departure_plan"] = True
                payload["control"]["departure"] = None
                client.apply_snapshot(payload)
                assert (control.supports_departure_plan, control.departure) == (True, None)
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
                    {"command": "departure_trip", "deadline": deadline.isoformat(), "source": "manual",
                     "withdraw": True, "expected_serial_number": serial},
                    {"command": "departure_inputs", "value": given.snapshot(), "expected_serial_number": serial},
                ]
                for broken in (
                    {"supports_departure_plan": 1}, {"departure": {}},
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
        finally:
            await service.stop()
    asyncio.run(run())
