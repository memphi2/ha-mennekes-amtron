"""The watch names a car only for a plug cycle it followed from end to end."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron_rtu import charge_watch as m
from custom_components.mennekes_amtron_rtu import vehicle_profiles
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
    hass = SimpleNamespace(data={})
    coordinator = Coordinator()
    meter = ChargeMeter()
    entry = SimpleNamespace(entry_id="entry", options={}, runtime_data=SimpleNamespace(
        coordinator=coordinator, identity=SimpleNamespace(serial_number="123", layout_version=0x0103),
    ))

    def configure(*, automatic=True, selected=None):
        entry.options = detection_catalog() if automatic else catalog()
        entry.options["active_vehicle_profile"] = selected
        entry.options["vehicle_profile_selected_at"] = START - 200
        return m.track_charge_watch(hass, entry)

    def poll(after, state=EvseState.IDLE, *, present=...):
        clock[0] += after
        meter.observe(
            clock[0], present=state is not EvseState.IDLE if present is ... else present,
            charging=state is EvseState.CHARGING,
        )
        coordinator.data.charge_meter = parse_charge_meter(meter.snapshot())
        coordinator.async_update_listeners()

    return SimpleNamespace(
        hass=hass, entry=entry, clock=clock, coordinator=coordinator,
        configure=configure, poll=poll, watch=m.charge_watch(hass, "entry"),
    )


def profile(entry, key):
    return vehicle_profiles.profiles(entry.options)[key]


def test_a_cycle_the_service_saw_whole_is_kept_with_its_car(scene):
    unsubscribe = scene.configure(automatic=False, selected=A)
    watch = scene.watch
    scene.poll(10)
    scene.poll(10, EvseState.EV_CONNECTED)
    plugged = scene.clock[0]
    scene.poll(10, EvseState.CHARGING)
    assert watch.last is None
    assert watch.owner(scene.entry) is None  # A car chosen by hand owns nothing until the end.
    scene.poll(10)
    unplugged = scene.clock[0]
    assert watch.last == m.PlugCycle(plugged, unplugged, profile(scene.entry, A))
    assert watch.owner(scene.entry) == profile(scene.entry, A)
    # Idle polls afterwards leave the cycle as it is.
    scene.poll(10)
    assert watch.last.unplugged_at == unplugged
    unsubscribe()
    assert not scene.coordinator.listeners
    # The same watch serves the entry after its reload, until it is forgotten.
    assert m.charge_watch(scene.hass, "entry") is watch
    m.forget_charge_watch(scene.hass, "entry")
    m.forget_charge_watch(scene.hass, "entry")
    assert m.charge_watch(scene.hass, "entry") is not watch


def test_a_cycle_that_began_unseen_is_neither_measured_nor_owned(scene):
    scene.configure()
    scene.poll(10, EvseState.CHARGING)
    m.confirm_automatic_vehicle(scene.hass, scene.entry, A)
    assert scene.watch.owner(scene.entry) is None
    scene.poll(10)
    assert scene.watch.last is None


def test_a_recognized_car_keeps_its_loss_settings_after_it_is_deselected(scene):
    scene.configure()
    entry, watch = scene.entry, scene.watch
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    m.confirm_automatic_vehicle(scene.hass, entry, "0" * 32)  # no such car
    assert watch.owner(entry) is None
    m.confirm_automatic_vehicle(scene.hass, entry, A)
    m.confirm_automatic_vehicle(scene.hass, entry, A)
    entry.options = vehicle_profiles.activate_profile(entry.options, A)
    scene.poll(10, EvseState.CHARGING)
    assert watch.owner(entry) == profile(entry, A)
    entry.options = vehicle_profiles.activate_profile(entry.options, None)
    assert m.loss_vehicle_options(scene.hass, entry)["vehicle_profile_id"] == A
    scene.poll(10)
    assert watch.last.owner == profile(entry, A)
    assert m.loss_vehicle_options(scene.hass, entry)["vehicle_profile_id"] == A
    # Its profile is edited afterwards: the evidence names a car that is gone.
    entry.options["vehicle_profiles"][A]["name"] = "Renamed"
    assert watch.owner(entry) is None
    assert "vehicle_profile_id" not in m.loss_vehicle_options(scene.hass, entry)


@pytest.mark.parametrize("second", ["other_car", "edited", "selected_other", "selection_off"])
def test_a_second_name_for_the_same_cycle_makes_it_nobodys(scene, second):
    scene.configure()
    entry = scene.entry
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    m.confirm_automatic_vehicle(scene.hass, entry, A)
    if second == "other_car":
        m.confirm_automatic_vehicle(scene.hass, entry, B)
    elif second == "edited":
        entry.options["vehicle_profiles"][A]["name"] = "Renamed"
    elif second == "selected_other":
        entry.options = vehicle_profiles.activate_profile(entry.options, B)
    else:
        entry.options["vehicle_auto_selection"] = False
    scene.poll(10, EvseState.CHARGING)
    assert scene.watch.owner(entry) is None
    m.confirm_automatic_vehicle(scene.hass, entry, A)
    scene.poll(10)
    assert scene.watch.last.owner is None


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
    assert (scene.watch.last.owner is not None) is owned


def test_a_cycle_that_ended_while_home_assistant_was_away_has_no_car(scene):
    scene.configure(automatic=False, selected=A)
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    # Home Assistant restarts: the service kept the cycle, the claim is gone.
    m.forget_charge_watch(scene.hass, "entry")
    watch = m.charge_watch(scene.hass, "entry")
    scene.poll(10)
    watch.observe(scene.entry)
    assert watch.last is not None
    assert watch.last.owner is None


def test_another_wallbox_or_an_unpolled_coordinator_is_no_evidence(scene):
    scene.configure(automatic=False, selected=A)
    scene.poll(10)
    scene.poll(10, EvseState.CHARGING)
    # A coordinator that has not polled yet, or an entry without runtime, says nothing.
    kept, scene.coordinator.data.charge_meter = scene.coordinator.data.charge_meter, None
    scene.coordinator.async_update_listeners()
    scene.watch.observe(SimpleNamespace(options={}))
    scene.coordinator.data.charge_meter = kept
    scene.poll(10)
    assert scene.watch.last is not None
    scene.entry.runtime_data.identity.serial_number = "456"
    scene.coordinator.data.charge_meter = parse_charge_meter(ChargeMeter().snapshot())
    scene.coordinator.async_update_listeners()
    assert scene.watch.last is None
