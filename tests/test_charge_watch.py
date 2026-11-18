"""The service keeps the car's name with the plug cycle Home Assistant named."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron_rtu import charge_watch as m
from custom_components.mennekes_amtron_rtu import vehicle_profiles
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from custom_components.mennekes_amtron_rtu.device.meter_log import (
    ChargeMeter,
    parse_charge_meter,
)
from tests.test_charge_loss_observer import Coordinator
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A, B, catalog

START = 1_700_000_000.0


@pytest.fixture
def scene(monkeypatch):
    """One wallbox with two cars, the service's charge meter and the watch."""
    clock = [START]
    monkeypatch.setattr(vehicle_profiles, "time", SimpleNamespace(time=lambda: clock[0]))
    loop = asyncio.new_event_loop()
    coordinator = Coordinator()
    meter = ChargeMeter()
    sent, failing = [], []

    async def execute(command, *, plugged_at, note):
        assert command == "charge_note"
        sent.append(note)
        if failing:
            raise failing.pop()
        meter.annotate(plugged_at, note)

    entry = SimpleNamespace(
        entry_id="entry", options={},
        async_create_background_task=lambda _hass, work, _name: loop.create_task(work),
        runtime_data=SimpleNamespace(
            coordinator=coordinator, client=SimpleNamespace(execute=execute), charge_watch=None,
        ),
    )

    def publish():
        """Let a sent note arrive, then show Home Assistant the service's state."""
        loop.run_until_complete(asyncio.sleep(0))
        loop.run_until_complete(asyncio.sleep(0))
        coordinator.data.charge_meter = parse_charge_meter(meter.snapshot())
        coordinator.async_update_listeners()
        loop.run_until_complete(asyncio.sleep(0))
        loop.run_until_complete(asyncio.sleep(0))

    def configure(*, automatic=True, selected=None):
        entry.options = detection_catalog() if automatic else catalog()
        entry.options["active_vehicle_profile"] = selected
        entry.options["vehicle_profile_selected_at"] = START - 200
        entry.runtime_data.charge_watch = watch = m.ChargeWatch(None, entry)
        return watch.start()

    def poll(after, state=EvseState.IDLE, *, present=...):
        clock[0] += after
        meter.observe(
            clock[0], present=state is not EvseState.IDLE if present is ... else present,
            charging=state is EvseState.CHARGING,
        )
        publish()

    def confirm(profile_id):
        m.confirm_automatic_vehicle(entry, profile_id)
        publish()

    yield SimpleNamespace(
        entry=entry, clock=clock, coordinator=coordinator, meter=meter, sent=sent,
        failing=failing, configure=configure, poll=poll, confirm=confirm, publish=publish,
    )
    loop.close()


def profile(entry, key):
    return vehicle_profiles.profiles(entry.options)[key]


def test_a_cycle_the_service_saw_whole_is_kept_with_its_car(scene):
    unsubscribe = scene.configure(automatic=False, selected=A)
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.EV_CONNECTED)
    plugged = scene.clock[0]
    scene.poll(10, EvseState.CHARGING)
    assert m.last_cycle(entry) is None
    assert m.owner(entry) is None  # A car chosen by hand owns nothing until the end.
    scene.poll(10)
    unplugged = scene.clock[0]
    assert m.last_cycle(entry) == m.PlugCycle(plugged, unplugged, profile(entry, A))
    assert m.owner(entry) == profile(entry, A)
    # The name was handed over once, and idle polls leave the cycle as it is.
    scene.poll(10)
    assert len(scene.sent) == 1
    assert m.last_cycle(entry).unplugged_at == unplugged
    unsubscribe()
    assert not scene.coordinator.listeners


def test_a_cycle_that_began_unseen_is_neither_measured_nor_owned(scene):
    scene.configure()
    scene.poll(10, EvseState.CHARGING)
    scene.confirm(A)
    assert m.owner(scene.entry) is None
    scene.poll(10)
    assert m.last_cycle(scene.entry) is None
    assert not scene.sent


def test_a_recognized_car_keeps_its_loss_settings_after_it_is_deselected(scene):
    scene.configure()
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    scene.confirm("0" * 32)  # no such car
    assert m.owner(entry) is None
    scene.confirm(A)
    scene.confirm(A)
    entry.options = vehicle_profiles.activate_profile(entry.options, A)
    scene.poll(10, EvseState.CHARGING)
    assert m.owner(entry) == profile(entry, A)
    entry.options = vehicle_profiles.activate_profile(entry.options, None)
    assert m.loss_vehicle_options(entry)["vehicle_profile_id"] == A
    scene.poll(10)
    assert m.last_cycle(entry).owner == profile(entry, A)
    assert m.loss_vehicle_options(entry)["vehicle_profile_id"] == A
    assert len(scene.sent) == 1
    # Its profile is edited afterwards: the evidence names a car that is gone.
    entry.options["vehicle_profiles"][A]["name"] = "Renamed"
    assert m.owner(entry) is None
    assert "vehicle_profile_id" not in m.loss_vehicle_options(entry)


@pytest.mark.parametrize("second", ["other_car", "edited", "selected_other", "selection_off"])
def test_a_second_name_for_the_same_cycle_makes_it_nobodys(scene, second):
    scene.configure()
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    scene.confirm(A)
    if second == "other_car":
        scene.confirm(B)
    elif second == "edited":
        entry.options["vehicle_profiles"][A]["name"] = "Renamed"
    elif second == "selected_other":
        entry.options = vehicle_profiles.activate_profile(entry.options, B)
    else:
        entry.options["vehicle_auto_selection"] = False
    scene.poll(10, EvseState.CHARGING)
    # The service shows the withdrawn name with its next state.
    scene.publish()
    assert m.owner(entry) is None
    scene.confirm(A)
    scene.poll(10)
    assert scene.meter.last_note == m.NOBODY
    assert m.last_cycle(entry).owner is None


@pytest.mark.parametrize(("selected_at", "owned"), [
    (START - 200, True), (START + 25, False), (None, False), (True, False),
])
def test_a_car_selected_by_hand_owns_only_a_charge_that_began_afterwards(scene, selected_at, owned):
    scene.configure(automatic=False)
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    entry.options["active_vehicle_profile"] = A
    entry.options["vehicle_profile_selected_at"] = selected_at
    scene.poll(10, EvseState.CHARGING)
    scene.poll(10)
    assert (m.last_cycle(entry).owner is not None) is owned


def test_the_name_survives_a_restart_of_home_assistant(scene):
    scene.configure()
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    scene.confirm(A)
    # Home Assistant restarts: the service kept the cycle and its note.
    scene.configure()
    assert m.owner(entry) == profile(entry, A)
    scene.poll(10)
    assert m.last_cycle(entry).owner == profile(entry, A)
    assert len(scene.sent) == 1


def test_a_name_the_service_did_not_take_is_sent_again(scene):
    scene.configure()
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    scene.failing.append(AmtronConnectionError("offline"))
    # A second recognition while the first is on its way sends nothing twice.
    m.confirm_automatic_vehicle(entry, A)
    scene.confirm(A)
    assert m.owner(entry) is None
    scene.poll(10, EvseState.CHARGING)
    scene.publish()
    assert m.owner(entry) == profile(entry, A)
    assert len(scene.sent) == 2
    # A name still on its way when the car leaves belongs to no later cycle.
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    scene.failing.extend([AmtronConnectionError("offline"), AmtronConnectionError("offline")])
    scene.confirm(B)
    assert len(scene.sent) == 4
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    scene.poll(10, EvseState.CHARGING)
    assert m.owner(entry) is None
    assert len(scene.sent) == 4


def test_an_unpolled_coordinator_or_an_entry_without_runtime_is_no_evidence(scene):
    scene.configure(automatic=False, selected=A)
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    kept, scene.coordinator.data.charge_meter = scene.coordinator.data.charge_meter, None
    scene.coordinator.async_update_listeners()
    scene.confirm(B)
    assert m.owner(scene.entry) is None
    assert m.last_cycle(scene.entry) is None
    assert m.owner(SimpleNamespace(options={})) is None
    assert "vehicle_profile_id" not in m.loss_vehicle_options(SimpleNamespace(options={}))
    scene.coordinator.data.charge_meter = kept
    scene.poll(10)
    assert m.last_cycle(scene.entry).owner == profile(scene.entry, A)
    # Nothing to tell a wallbox that has no watch, such as one being set up.
    scene.entry.runtime_data.charge_watch = None
    m.confirm_automatic_vehicle(scene.entry, A)
    m.confirm_automatic_vehicle(SimpleNamespace(options={}), A)


@pytest.mark.parametrize("note", ["", "q:0:" + A, "a:0000000000000000:" + A, "a:" + A])
def test_a_note_that_names_no_current_profile_is_nobody(scene, note):
    scene.configure()
    assert m._named(scene.entry.options, note) is None
