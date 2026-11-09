"""The selected car's rules apply only to a plug-in that car confirmed."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.mennekes_amtron_rtu import vehicle_binding as m
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.test_vehicle_detection import detection_catalog
from tests.test_vehicle_profile_runtime import A, B


class Coordinator:
    def __init__(self):
        self.last_update_success = True
        self.data = WallboxData(values={})
        self.listeners = []

    def current_data(self):
        return self.data

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def plug(self, connected):
        self.data = WallboxData(values={
            "evse_state": int(EvseState.EV_CONNECTED if connected else EvseState.IDLE),
            "cp_state": int(CpState.B1 if connected else CpState.A1),
        })
        for listener in list(self.listeners):
            listener()


def binding_entry(*, selected=A):
    options = detection_catalog()
    options["active_vehicle_profile"] = selected
    coordinator = Coordinator()
    return SimpleNamespace(
        entry_id="entry", options=options,
        runtime_data=SimpleNamespace(
            coordinator=coordinator,
            identity=SimpleNamespace(serial_number="serial", layout_version=0x0102),
        ),
    ), coordinator


def report(hass, *, plugged=True, home="home", name="first"):
    hass.states.async_set(f"device_tracker.{name}", home, force_update=True)
    hass.states.async_set(f"binary_sensor.{name}_plug", "on" if plugged else "off", force_update=True)


async def settle(hass):
    await hass.async_block_till_done()
    await asyncio.sleep(0)
    await hass.async_block_till_done()


def test_a_plug_in_binds_only_after_the_cars_own_fresh_report(tmp_path, caplog):
    caplog.set_level(logging.INFO)

    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry()
        report(hass)
        report(hass, plugged=False, name="second")
        binding = m.VehicleBinding(hass, entry)
        changes = []
        unsubscribe = binding.subscribe(lambda: changes.append(binding.resting))
        await binding.async_start()
        # Without a car, nothing rests and nothing is confirmed.
        coordinator.plug(False)
        await settle(hass)
        assert not binding.resting
        assert binding.confirmed is None
        # A plug-in after the car's last report is not proven to be that car.
        coordinator.plug(True)
        await settle(hass)
        assert binding.resting
        assert binding.confirmed is False
        assert "not confirmed as First car" in caplog.text
        # Its own report after the plug-in confirms it.
        report(hass)
        await settle(hass)
        assert not binding.resting
        assert binding.confirmed is True
        assert "First car confirmed at the wallbox" in caplog.text
        assert changes == [True, False]
        # The unplug ends the binding; an old report cannot renew it.
        coordinator.plug(False)
        await settle(hass)
        coordinator.plug(True)
        await settle(hass)
        assert binding.resting
        unsubscribe()
        unsubscribe()
        # A report while away does not confirm the car either.
        report(hass, home="not_home")
        await settle(hass)
        assert binding.resting
        binding.stop()
        await hass.async_stop()

    asyncio.run(run())


def test_the_binding_survives_a_restart_but_not_a_car_change(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry()
        report(hass, plugged=False, name="second")
        binding = m.VehicleBinding(hass, entry)
        await binding.async_start()
        coordinator.plug(False)
        await settle(hass)
        coordinator.plug(True)
        report(hass)
        await settle(hass)
        assert not binding.resting
        binding.stop()
        restarted = m.VehicleBinding(hass, entry)
        await restarted.async_start()
        assert not restarted.resting
        # Another selected car needs its own confirmation.
        entry.options["active_vehicle_profile"] = B
        assert restarted.resting
        report(hass, name="second")
        await settle(hass)
        assert not restarted.resting
        restarted.stop()
        await hass.async_stop()

    asyncio.run(run())


def test_no_selection_unknown_wallbox_or_no_detection_never_rests(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry(selected=None)
        binding = m.VehicleBinding(hass, entry)
        await binding.async_start()
        coordinator.plug(True)
        assert not binding.resting
        assert binding.confirmed is None
        entry.options["active_vehicle_profile"] = A
        coordinator.last_update_success = False
        assert not binding.resting
        coordinator.last_update_success = True
        entry.options["vehicle_profiles"][A].pop("detection")
        assert not binding.resting
        binding.stop()
        await hass.async_stop()

    asyncio.run(run())


def test_a_car_that_never_reported_an_unplug_is_confirmed_once(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry()
        report(hass)
        report(hass, plugged=False, name="second")
        binding = m.VehicleBinding(hass, entry)
        await binding.async_start()
        # The first start after an update knows no empty wallbox yet.
        coordinator.plug(True)
        await settle(hass)
        assert not binding.resting
        binding.stop()
        await hass.async_stop()

    asyncio.run(run())


def test_a_car_still_reporting_itself_plugged_in_is_called_stale(tmp_path, caplog):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry()
        binding = m.VehicleBinding(hass, entry)
        await binding.async_start()
        coordinator.plug(False)
        binding._first_empty = dt_util.utcnow() - timedelta(minutes=15)
        report(hass)
        await settle(hass)
        assert "First car still reports itself plugged in" in caplog.text
        caplog.clear()
        report(hass)
        await settle(hass)
        assert "still reports" not in caplog.text
        binding.stop()
        await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("raw", [
    None, {"absent_at": None}, {"absent_at": None, "vehicle": ""},
    {"absent_at": 1, "vehicle": None}, {"absent_at": "never", "vehicle": None},
    {"absent_at": "2026-01-01T00:00:00", "vehicle": None},
])
def test_invalid_saved_bindings_are_rejected(raw):
    with pytest.raises(ValueError, match="vehicle binding"):
        m.VehicleBindingState.restore(raw)


def test_storage_problems_only_forget_the_binding(tmp_path, caplog):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        entry, coordinator = binding_entry()
        with patch.object(m.VehicleBindingStore, "async_load", side_effect=ValueError("broken")):
            binding = m.VehicleBinding(hass, entry)
            await binding.async_start()
        with patch.object(m.VehicleBindingStore, "async_save", side_effect=OSError("full")):
            coordinator.plug(False)
            coordinator.plug(True)
            await settle(hass)
        assert "Ignoring saved vehicle binding" in caplog.text
        assert "Vehicle binding not saved" in caplog.text
        binding.stop()
        await hass.async_stop()

    asyncio.run(run())


def test_a_saved_binding_round_trips():
    for state in (m.VehicleBindingState(None, A), m.VehicleBindingState(dt_util.utcnow(), None)):
        restored = m.VehicleBindingState.restore(state.snapshot())
        assert (restored.absent_at, restored.vehicle) == (state.absent_at, state.vehicle)
