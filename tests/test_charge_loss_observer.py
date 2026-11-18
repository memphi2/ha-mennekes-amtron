"""Optional loss estimates do not own controls or guess historical car sessions."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import atomicwrites
import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu import goal_store
from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_AUTO_MATCH,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
)
from custom_components.mennekes_amtron_rtu.charge_loss_observer import (
    ChargeLossObserver,
    vehicle_energy,
)
from custom_components.mennekes_amtron_rtu.charge_watch import PlugCycle, note_for
from custom_components.mennekes_amtron_rtu.device.charge_loss import Charge
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.meter_log import MeterLog
from custom_components.mennekes_amtron_rtu.device.registers import (
    CP_STATE,
    EVSE_STATE,
)
from custom_components.mennekes_amtron_rtu.vehicle_profiles import VehicleProfile
from custom_components.mennekes_amtron_rtu.vehicle_sources.types import ChargeRecord
from tests.test_bavarian_history import VIN, vehicle_registry

READ = "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_latest"
LATER = "custom_components.mennekes_amtron_rtu.charge_loss_observer.async_call_later"


class Coordinator:
    def __init__(self):
        self.data = WallboxData(
            values={EVSE_STATE.key: EvseState.IDLE, CP_STATE.key: CpState.A1},
            energy_observed_at=1.0,
        )
        self.last_update_success = True
        self.listeners = []
        # The service's meter log, the entry's watch and the last charge in both.
        self.meter = MeterLog()
        self.client = SimpleNamespace(async_energy_between=AsyncMock(
            side_effect=lambda start, end: self.meter.energy_between(start, end),
        ))
        self.history = False
        self.owner = None
        self.total = 100.0
        self.span = None

    def current_data(self):
        return self.data

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def async_update_listeners(self):
        for listener in tuple(self.listeners):
            listener()

    def complete(self, *, at=None, energy=10):
        """One plug cycle; the meter rises between the vehicle's own bounds."""
        at = time.time() if at is None else at
        for moment, kwh in (
            (at - 100, self.total), (at - 95, self.total),
            (at - 5, self.total + energy), (at, self.total + energy),
        ):
            self.meter.observe(moment, kwh)
        self.total += energy
        self.span = (at - 95, at - 5)
        self.cycle(at - 100, at)
        if self.history:
            self.data.values[EVSE_STATE.key] = EvseState.CHARGING
            self.async_update_listeners()
            self.data.values[EVSE_STATE.key] = EvseState.IDLE
        self.async_update_listeners()
        return Charge(at - 100, at, energy)

    def cycle(self, plugged_at, unplugged_at, owner=...):
        """What the service reports of its charge meter, and whose cycle it was."""
        owner = self.owner if owner is ... else owner
        self.last = PlugCycle(plugged_at, unplugged_at, owner)
        self.data.charge_meter = {
            "known_since": None, "present": False,
            "plugged_at": None, "charging_at": None, "note": None,
            "last": (plugged_at, unplugged_at),
            "last_note": note_for(owner, automatic=True) if isinstance(owner, VehicleProfile) else None,
        }

    def record(self, vehicle_kwh=8):
        """What the vehicle recorded for the last charge."""
        return ChargeRecord(*self.span, vehicle_kwh) if self.span is not None else None

    def reader(self, vehicle_kwh=8):
        return AsyncMock(side_effect=lambda *_args, **_kwargs: self.record(vehicle_kwh))


def build(hass, mode="sensor", *, auto=False):
    if mode == "vehicle_history":
        hass.data.setdefault(dr.DATA_REGISTRY, vehicle_registry())
    coordinator = Coordinator()
    coordinator.history = mode == "vehicle_history"
    entry = SimpleNamespace(
        entry_id="entry", options={
            CONF_CHARGE_LOSS_SOURCE: mode, CONF_LOSS_AUTO_MATCH: auto,
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_energy",
            "charge_loss_vehicle_provider": "bavariandata",
            "charge_loss_vehicle_device": "car-device",
        }, runtime_data=SimpleNamespace(
            coordinator=coordinator, identity=SimpleNamespace(serial_number="123", layout_version=0x0103),
            client=coordinator.client,
        ),
    )
    if mode == "vehicle_history":
        entry.options.update({
            "charge_loss_vehicle_provider": "bavariandata", "charge_loss_vehicle_device": "car-device",
        })
    return ChargeLossObserver(hass, entry), coordinator


@pytest.mark.parametrize(("state", "unit", "restored", "expected"), [
    ("7.5", "kWh", False, 7.5), ("7500", "Wh", False, 7.5),
    ("7", None, False, None), ("7", "kWh", True, None),
    ("nan", "kWh", False, None), ("inf", "kWh", False, None),
    ("-1", "kWh", False, None), ("0", "kWh", False, None),
    ("unknown", "kWh", False, None),
])
def test_energy_units_and_restored_values(state, unit, restored, expected):
    assert vehicle_energy(State("sensor.energy", state, {"unit_of_measurement": unit, "restored": restored})) == expected
    assert vehicle_energy(None) is None


def test_manual_assignment_is_optional_durable_and_correctable(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        try:
            await observer.async_start()
            assert not observer.can_assign
            with pytest.raises(ServiceValidationError) as error:
                await observer.async_assign()
            assert error.value.translation_key == "loss_unavailable"
            coordinator.complete()
            await hass.async_block_till_done()
            assert observer.can_assign
            assert observer.statistics.last_loss_percent is None
            with pytest.raises(ServiceValidationError) as error:
                await observer.async_assign()
            assert error.value.translation_key == "loss_energy_invalid"
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert observer.statistics.last_loss_percent is None
            await observer.async_assign()
            assert observer.statistics.last_loss_percent == pytest.approx(20)
            assert observer.statistics.association == "manual"
            await observer.async_assign()
            assert observer.statistics.sample_count == 1
            hass.states.async_set("sensor.car_energy", "9", {"unit_of_measurement": "kWh"})
            await observer.async_assign()
            assert observer.statistics.last_loss_percent == pytest.approx(10)
            assert observer.statistics.sample_count == 1
            await observer.async_stop()
            assert not coordinator.listeners
            replacement, replacement_coordinator = build(hass)
            replacement_coordinator.data = coordinator.data
            await replacement.async_start()
            assert replacement.statistics.last_loss_percent == pytest.approx(10)
            await replacement.async_stop()
            observer._queue()
            assert not observer._pending
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_auto_only_live_changed_values_and_no_old_session_replay(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, auto=True)
        hass.states.async_set("sensor.car_energy", "7", {"unit_of_measurement": "kWh"})
        coordinator.complete(at=time.time() - 1000)
        try:
            await observer.async_start()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert observer.statistics.last_loss_percent is None
            coordinator.complete()
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh", "changed": True})
            await hass.async_block_till_done()
            assert observer.statistics.last_loss_percent is None
            hass.states.async_set("sensor.car_energy", "unknown")
            hass.states.async_set("sensor.car_energy", "9", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert observer.statistics.last_loss_percent is None
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert observer.statistics.last_loss_percent == pytest.approx(20)
            assert observer.statistics.association == "changed_value"
            coordinator.data.values[EVSE_STATE.key] = EvseState.EV_CONNECTED
            coordinator.data.values[CP_STATE.key] = CpState.B1
            assert not observer.can_assign
            hass.states.async_set("sensor.car_energy", "6", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert observer.statistics.last_loss_percent == pytest.approx(20)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_history_measures_the_vehicle_record_retries_and_does_not_call_on_each_poll(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        callbacks = []
        cancellations = []
        def schedule(_hass, delay, callback):
            callbacks.append((delay, callback))
            return lambda: cancellations.append(delay)
        with patch(READ, new=AsyncMock(side_effect=[TimeoutError, HomeAssistantError(), None])) as read, patch(LATER, side_effect=schedule):
            try:
                await observer.async_start()
                # Without a meter reading there is no span to ask the vehicle about.
                read.assert_not_awaited()
                assert [delay for delay, _ in callbacks] == [60]
                coordinator.complete()
                await hass.async_block_till_done()
                assert cancellations == [60]
                assert [delay for delay, _ in callbacks] == [60, 60]
                observer._schedule_retry()
                assert len(callbacks) == 2
                for expected in (300, 1800):
                    callbacks[-1][1](None)
                    await hass.async_block_till_done()
                    assert callbacks[-1][0] == expected
                read.side_effect = None
                read.return_value = coordinator.record(8)
                callbacks[-1][1](None)
                await hass.async_block_till_done()
                assert observer.statistics.last_loss_percent == pytest.approx(20)
                assert observer.statistics.wallbox_kwh == pytest.approx(10)
                assert observer.statistics.association == "history"
                observer._schedule_retry()
                assert len(callbacks) == 4
                coordinator.async_update_listeners()
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert read.await_count == 4
                # The vehicle corrects its record: same charge, new result.
                observer._last_query = float("-inf")
                read.return_value = coordinator.record(9)
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert observer.statistics.last_loss_percent == pytest.approx(10)
                assert observer.statistics.sample_count == 1
                with patch.object(observer._store, "async_save") as save:
                    observer._last_query = float("-inf")
                    hass.bus.async_fire("bavariandata_charging_stopped", {})
                    await hass.async_block_till_done()
                    assert read.await_count == 6
                    save.assert_not_awaited()
                # The record of the next charge is not there yet.
                read.return_value = None
                coordinator.complete(at=time.time()+200)
                await hass.async_block_till_done()
                assert callbacks[-1][0] == 60
                observer._retry = lambda: cancellations.append(1800)
                coordinator.complete(at=time.time()+400)
                await hass.async_block_till_done()
                assert cancellations[-1] == 1800
                # More in the car than the meter delivered cannot be a loss.
                read.return_value = coordinator.record(11)
                observer._last_query = float("-inf")
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert observer.statistics.status == "invalid"
                assert observer.statistics.last_loss_percent is None
                assert observer.statistics.average_loss_percent == pytest.approx(10)
            finally:
                await observer.async_stop()
                await hass.async_stop()
    asyncio.run(run())


def test_the_span_the_vehicle_names_is_measured_once_the_meter_covers_it(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        data, meter = coordinator.data, coordinator.meter
        data.energy_observed_at = None
        assert not observer.available

        def poll(at, kwh, state):
            meter.observe(at, kwh)
            data.values[EVSE_STATE.key] = state
            data.energy_observed_at = at
            data.charge_meter = {
                "known_since": 1000, "present": True,
                "plugged_at": plugged[0], "charging_at": None, "note": None, "last": None, "last_note": None,
            }
            coordinator.async_update_listeners()

        plugged = [1030]

        client = observer._entry.runtime_data.client
        with patch(READ, new=AsyncMock(return_value=None)) as read, patch(LATER, return_value=lambda: None):
            try:
                await observer.async_start()
                poll(1000, 500, EvseState.IDLE)
                assert observer.available
                poll(1040, 500, EvseState.CHARGING)
                poll(1100, 506, EvseState.CHARGING)
                read.return_value = ChargeRecord(1035, 1130, 5.4)
                poll(1120, 506, EvseState.IDLE)
                await hass.async_block_till_done()
                # The reading after the record's end is still to come.
                assert read.await_args.kwargs["since"] == 1030 - 120
                assert observer.statistics.sample_count == 0
                # The service does not answer: nothing is measured, nothing breaks.
                observer._last_query = float("-inf")
                poll(1150, 506, EvseState.IDLE)
                answer, client.async_energy_between.side_effect = client.async_energy_between.side_effect, AmtronConnectionError("down")
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert observer.statistics.sample_count == 0
                client.async_energy_between.side_effect = answer
                observer._last_query = float("-inf")
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert observer.statistics.wallbox_kwh == pytest.approx(6)
                assert observer.statistics.vehicle_kwh == pytest.approx(5.4)
                assert observer.statistics.last_loss_percent == pytest.approx(10)
                # A record of a span without delivered energy measures nothing.
                read.return_value = ChargeRecord(1125, 1145, 2)
                observer._last_query = float("-inf")
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert observer.statistics.sample_count == 1
                assert observer.statistics.last_loss_percent == pytest.approx(10)
                # A record from before this plug-in was charged elsewhere,
                # whatever another car drew here meanwhile; so was one of a
                # plug-in the service did not see.
                for plugged[0], record in ((1400, ChargeRecord(1200, 1390, 3)), (None, ChargeRecord(1410, 1490, 3))):
                    for at, kwh in ((1200, 506), (1390, 510), (1410, 510), (1490, 514), (1500, 514)):
                        meter.observe(at, kwh)
                    read.return_value = record
                    observer._last_query = float("-inf")
                    asked = client.async_energy_between.await_count
                    poll(1500 + (plugged[0] or 0), 514, EvseState.IDLE)
                    hass.bus.async_fire("bavariandata_charging_stopped", {})
                    await hass.async_block_till_done()
                    assert client.async_energy_between.await_count == asked
                    assert observer.statistics.sample_count == 1
            finally:
                await observer.async_stop()
                await hass.async_stop()
    asyncio.run(run())


def test_provider_exception_during_setup_does_not_block_wallbox_and_retries(tmp_path, caplog):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        coordinator.complete()
        callbacks = []

        def schedule(_hass, delay, callback):
            callbacks.append((delay, callback))
            return lambda: None

        with (
            patch(READ, new=AsyncMock(side_effect=[ValueError("private vehicle data"), coordinator.record(8)])),
            patch(LATER, side_effect=schedule),
        ):
            try:
                await observer.async_start()
                assert observer.statistics.last_loss_percent is None
                assert callbacks[0][0] == 60
                assert "ValueError" in caplog.text
                assert "private vehicle data" not in caplog.text
                callbacks[0][1](None)
                await hass.async_block_till_done()
                assert observer.statistics.last_loss_percent == pytest.approx(20)
            finally:
                await observer.async_stop()
                await hass.async_stop()

    asyncio.run(run())


def test_storage_failures_never_modify_a_saved_match_or_break_controls(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        try:
            await observer.async_start()
            coordinator.complete()
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})
            with patch.object(observer._store, "async_save", side_effect=OSError):
                with pytest.raises(ServiceValidationError) as error:
                    await observer.async_assign()
                assert error.value.translation_key == "loss_storage_unavailable"
                assert observer.statistics.sample_count == 0
                assert not observer.available
                observer._last_storage_attempt = float("-inf")
                coordinator.async_update_listeners()
                await hass.async_block_till_done()
            observer._last_storage_attempt = float("-inf")
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer.available
            await observer.async_assign()
            assert observer.statistics.sample_count == 1
            await observer.async_stop()
            broken, coord2 = build(hass)
            with patch.object(broken._store, "async_load", side_effect=OSError):
                await broken.async_start()
                assert not broken.available
                coord2.complete(at=time.time() + 200)
                await hass.async_block_till_done()
                assert broken._task is None
                await broken.async_stop()
            # Estimates saved in a form nobody reads any more: start without them.
            fresh, _coord3 = build(hass)
            with patch.object(fresh._store, "async_load", side_effect=ValueError):
                await fresh.async_start()
                assert fresh.available
                assert fresh.statistics.sample_count == 0
                await fresh.async_stop()
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["file_sync", "directory_sync"])
def test_assignment_sync_failure_keeps_visible_aggregate_and_reports_failure(tmp_path, monkeypatch, failure):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        try:
            await observer.async_start()
            coordinator.complete()
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})

            def fail(*_args):
                raise OSError("injected fsync failure")

            with monkeypatch.context() as scope:
                scope.setattr(atomicwrites, "_proper_fsync" if failure == "file_sync" else "_sync_directory", fail)
                with pytest.raises(ServiceValidationError) as error:
                    await observer.async_assign()
                assert error.value.translation_key == "loss_storage_unavailable"
            visible = await observer._store.async_load()
            assert observer.statistics.snapshot() == visible.snapshot()
            assert observer.statistics.sample_count == (1 if failure == "directory_sync" else 0)
            assert not observer.available
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_unknown_assignment_is_unavailable_until_pending_candidate_is_saved(tmp_path, monkeypatch):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        try:
            await observer.async_start()
            coordinator.complete()
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})

            def fail(*_args):
                raise OSError("injected failure")

            with monkeypatch.context() as scope:
                scope.setattr(atomicwrites, "_sync_directory", fail)
                scope.setattr(observer._store, "_validate_file", fail)
                with pytest.raises(ServiceValidationError) as error:
                    await observer.async_assign()
                assert error.value.translation_key == "loss_storage_unavailable"
            assert not observer.available
            assert observer.statistics.last_loss_percent == pytest.approx(20)
            observer._last_storage_attempt -= 61
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer.available
            assert (await observer._store.async_load()).snapshot() == observer.statistics.snapshot()
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_cancelled_assignment_keeps_memory_and_durable_loss_in_sync(tmp_path, monkeypatch, cancel_count):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        started, release = threading.Event(), threading.Event()
        writer = goal_store.write_utf8_file_atomic

        def delayed(*args, **kwargs):
            started.set()
            assert release.wait(5)
            writer(*args, **kwargs)

        try:
            await observer.async_start()
            coordinator.complete()
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})
            monkeypatch.setattr(goal_store, "write_utf8_file_atomic", delayed)
            task = asyncio.create_task(observer.async_assign())
            try:
                assert await asyncio.to_thread(started.wait, 5)
                for _ in range(cancel_count):
                    task.cancel()
                    await asyncio.sleep(0)
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observer.statistics.last_loss_percent == pytest.approx(20)
            assert (await observer._store.async_load()).snapshot() == observer.statistics.snapshot()
        finally:
            release.set()
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def _levels(caplog, text):
    return [record.levelno for record in caplog.records if record.getMessage() == text]


@pytest.mark.parametrize("failed_attempts", [1, 2])
def test_history_match_is_retried_after_storage_recovers_without_poll_churn(tmp_path, caplog, failed_attempts):
    async def run():
        caplog.set_level(logging.DEBUG, logger="custom_components.mennekes_amtron_rtu.charge_loss_observer")
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        with patch(READ, new=coordinator.reader()) as read:
            try:
                await observer.async_start()
                with patch.object(observer._store, "async_save", side_effect=OSError) as failed_save:
                    coordinator.complete(energy=10)
                    await hass.async_block_till_done()
                    for attempt in range(failed_attempts):
                        if attempt:
                            observer._last_storage_attempt -= 61
                            coordinator.async_update_listeners()
                            await hass.async_block_till_done()
                        assert not observer.available
                        assert observer.statistics.sample_count == 0
                        assert read.await_count == failed_save.await_count == attempt + 1
                        # Polling during the retry interval neither queries the
                        # vehicle history again nor attempts another disk write.
                        for _ in range(5):
                            coordinator.async_update_listeners()
                            await hass.async_block_till_done()
                        assert read.await_count == failed_save.await_count == attempt + 1

                observer._last_storage_attempt -= 61
                coordinator.async_update_listeners()
                await hass.async_block_till_done()
                assert observer.available
                assert observer.statistics.last_loss_percent == pytest.approx(20)
                assert observer.statistics.sample_count == 1
                assert observer.statistics.association == "history"
                assert read.await_count == failed_attempts + 1
                restored = await observer._store.async_load()
                assert restored.snapshot() == observer.statistics.snapshot()
                # The failure warns once, its repeats stay at debug, and the
                # recovery is reported once.
                assert _levels(caplog, "Charging loss: estimate not saved; retrying without affecting charging") == (
                    [logging.WARNING] + [logging.DEBUG] * (failed_attempts - 1)
                )
                assert _levels(caplog, "Charging loss: estimate saved again") == [logging.INFO]

                with patch.object(observer._store, "async_save", wraps=observer._store.async_save) as save:
                    for _ in range(5):
                        coordinator.async_update_listeners()
                        await hass.async_block_till_done()
                    save.assert_not_awaited()
                    assert read.await_count == failed_attempts + 1
                assert _levels(caplog, "Charging loss: estimate saved again") == [logging.INFO]
            finally:
                await observer.async_stop()
                await hass.async_stop()

    asyncio.run(run())


def test_inflight_history_query_cancels_on_unload(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        started = asyncio.Event()
        async def read(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch(READ, side_effect=read):
            try:
                await observer.async_start()
                coordinator.complete()
                await started.wait()
                await observer.async_stop()
                assert observer._task.done()
                assert observer.statistics.sample_count == 0
            finally:
                await observer.async_stop()
                await hass.async_stop()
    asyncio.run(run())


def test_account_or_rebound_device_is_unavailable_and_never_matches_a_different_car(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "account-entry")})
        observer, coordinator = build(hass, "vehicle_history")
        with patch(READ, new=AsyncMock()) as read:
            try:
                await observer.async_start()
                coordinator.complete()
                await hass.async_block_till_done()
                assert not observer.available
                read.assert_not_awaited()
            finally:
                await observer.async_stop()
        hass.data[dr.DATA_REGISTRY] = vehicle_registry()
        observer, _coordinator = build(hass, "vehicle_history")
        assert observer.available
        assert VIN not in observer._source
        hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
        assert not observer.available
        await observer.async_stop()
        await hass.async_stop()
    asyncio.run(run())


def test_generic_history_selection_keeps_the_existing_average_after_reload(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        original, coordinator = build(hass, "vehicle_history")
        replacement = None
        try:
            with patch(READ, new=coordinator.reader()):
                await original.async_start()
                coordinator.complete(energy=10)
                await hass.async_block_till_done()
                assert original.statistics.sample_count == 1
                await original.async_stop()
                replacement, _coordinator = build(hass, "vehicle_history")
                await replacement.async_start()
                assert replacement.statistics.average_loss_percent == pytest.approx(20)
                assert replacement.statistics.sample_count == 1
                assert replacement.statistics.source == original.statistics.source
        finally:
            await original.async_stop()
            if replacement is not None:
                await replacement.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_temporarily_missing_history_vehicle_preserves_statistics_and_recovers(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        original, coordinator = build(hass, "vehicle_history")
        replacement = None
        with patch(READ, new=coordinator.reader()) as read:
            try:
                await original.async_start()
                coordinator.complete(energy=10)
                await hass.async_block_till_done()
                expected = original.statistics.snapshot()
                assert original.statistics.sample_count == 1
                await original.async_stop()
                saved = original._store.path.read_bytes()

                # The selected HA device is temporarily absent/invalid on restart.
                hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "account-entry")})
                replacement, replacement_coordinator = build(hass, "vehicle_history")
                replacement_coordinator.data = coordinator.data
                await replacement.async_start()
                assert not replacement.available
                assert replacement.statistics.snapshot() == expected
                assert original._store.path.read_bytes() == saved
                assert read.await_count == 1

                # A registry event recovers even when identical wallbox polls
                # do not notify coordinator listeners.
                hass.data[dr.DATA_REGISTRY] = vehicle_registry()
                hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "car-device", "action": "create"})
                await hass.async_block_till_done()
                assert replacement.available
                assert replacement.statistics.sample_count == 1
                assert replacement.statistics.average_loss_percent == pytest.approx(20)
                assert replacement.statistics.source == expected["source"]

                # The same registry ID acquiring another VIN cannot rebind the
                # live observer or replace the established statistics.
                before_rebind = replacement.statistics.snapshot()
                hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
                hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "car-device", "action": "update"})
                await hass.async_block_till_done()
                assert not replacement.available
                assert replacement.statistics.snapshot() == before_rebind
                hass.data[dr.DATA_REGISTRY] = vehicle_registry()
                hass.bus.async_fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"device_id": "car-device", "action": "update"})
                await hass.async_block_till_done()
                assert replacement.available
                assert replacement.statistics.source == expected["source"]
            finally:
                await original.async_stop()
                if replacement is not None:
                    await replacement.async_stop()
                await hass.async_stop()
    asyncio.run(run())


def test_explicitly_disabled_history_provider_cannot_match_a_retained_legacy_vehicle(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        old, coordinator = build(hass, "vehicle_history")
        old._entry.options["charge_loss_vehicle_provider"] = "none"
        observer = ChargeLossObserver(hass, old._entry)
        try:
            with patch(READ) as read:
                await observer.async_start()
                coordinator.complete()
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert not observer.available
                assert not observer.can_assign
                assert observer.statistics.sample_count == 0
                read.assert_not_called()
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_consumed_auto_candidate_cannot_replay_over_a_manual_correction(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, auto=True)
        hass.states.async_set("sensor.car_energy", "7", {"unit_of_measurement": "kWh"})
        try:
            await observer.async_start()
            coordinator.complete()
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car_energy", "8", {"unit_of_measurement": "kWh"})
            await hass.async_block_till_done()
            assert observer.statistics.vehicle_kwh == 8
            assert observer._change is None
            hass.states.async_set("sensor.car_energy", "unknown")
            hass.states.async_set("sensor.car_energy", "9", {"unit_of_measurement": "kWh"})
            await observer.async_assign()
            observer._queue()
            await hass.async_block_till_done()
            assert observer.statistics.vehicle_kwh == 9
            assert observer.statistics.association == "manual"
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["sensor", "vehicle_history"])
def test_a_result_that_waited_behind_another_save_is_dropped_at_unload(tmp_path, mode):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, mode, auto=True)
        with patch(READ, new=coordinator.reader()), patch(LATER, return_value=lambda: None):
            try:
                await observer.async_start()
                await observer._lock.acquire()
                coordinator.complete()
                await asyncio.sleep(0)
                # A second trigger while the first still runs starts no second task.
                task = observer._task
                observer._queue()
                assert observer._task is task
                # The entry is unloaded by the time the result may be saved.
                for _ in range(5):
                    await asyncio.sleep(0)
                observer._stopping = True
                observer._lock.release()
                await hass.async_block_till_done()
                assert observer.statistics.sample_count == 0
                assert observer.statistics.status == "waiting_for_session"
            finally:
                await observer.async_stop()
                await hass.async_stop()
    asyncio.run(run())


def test_the_plug_cycle_of_this_car_is_taken_once_the_meter_covers_it(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        observer._profiled, observer._profile_id = True, "a" * 32
        meter, now = coordinator.meter, time.time()
        mine = SimpleNamespace(id="a" * 32)

        async def cycle(plugged_at, unplugged_at):
            coordinator.cycle(plugged_at, unplugged_at, mine)
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            return coordinator.last

        # The cars here are no configured profiles: name the owner directly.
        named = patch(
            "custom_components.mennekes_amtron_rtu.charge_loss_observer.last_cycle",
            side_effect=lambda _entry: getattr(coordinator, "last", None),
        )
        named.start()
        try:
            await observer.async_start()
            # Another car's cycle, and one nobody was recognized for.
            for owner in (SimpleNamespace(id="b" * 32), None):
                coordinator.owner = owner
                coordinator.complete(at=now - 5000 + (0 if owner else 500))
                await hass.async_block_till_done()
                assert observer._charge is None
                assert observer.statistics.status == "waiting_for_session"
            # The reading after the unplug is still to come ...
            meter.observe(now - 300, coordinator.total)
            waiting = await cycle(now - 300, now - 10)
            assert observer._cycle != waiting
            # ... and a plug-in without charging leaves everything as it was.
            meter.observe(now - 5, coordinator.total)
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer._cycle == waiting
            assert observer._charge is None
            # A span the meter log can no longer place is given up.
            lost = await cycle(now - 9000, now - 8000)
            assert observer._cycle == lost
            coordinator.owner = mine
            charge = coordinator.complete(at=now)
            await hass.async_block_till_done()
            assert observer._charge == charge
            assert observer.statistics.status == "waiting_for_vehicle"
            # Seen again after a restart, it is accounted for: nothing is asked.
            client = observer._entry.runtime_data.client
            answer, client.async_energy_between.side_effect = client.async_energy_between.side_effect, AmtronConnectionError("down")
            asked = client.async_energy_between.await_count
            observer._cycle = observer._charge = None
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert client.async_energy_between.await_count == asked
            assert observer._charge == charge
            # A service that does not answer has measured nothing: asked
            # again, and meanwhile nothing goes to the charge before.
            assert observer._assignable == charge
            unanswered = await cycle(now - 9500, now - 8500)
            assert observer._cycle != unanswered
            assert observer._assignable is None
            assert observer.statistics.status == "waiting_for_vehicle"
            client.async_energy_between.side_effect = answer
            # A cycle the meter cannot measure is no longer the charge before.
            # A save that fails is tried again by the ordinary storage retry.
            with patch.object(observer._store, "async_save", AsyncMock(side_effect=OSError("full"))):
                coordinator.async_update_listeners()
                await hass.async_block_till_done()
            assert observer._cycle == unanswered
            assert observer._booking is not None
            assert observer._assignable is None
            assert not observer.available
            observer._last_storage_attempt = float("-inf")
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer.available
            assert observer.statistics.status == "waiting_for_session"
        finally:
            named.stop()
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_a_charge_that_could_not_be_saved_is_booked_whatever_came_after(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass)
        observer._profiled, observer._profile_id = True, "a" * 32
        mine, now = SimpleNamespace(id="a" * 32), time.time()
        named = patch(
            "custom_components.mennekes_amtron_rtu.charge_loss_observer.last_cycle",
            side_effect=lambda _entry: getattr(coordinator, "last", None),
        )
        named.start()
        try:
            await observer.async_start()
            coordinator.owner = mine
            saves = AsyncMock(side_effect=OSError("full"))
            with patch.object(observer._store, "async_save", saves):
                charge = coordinator.complete(at=now - 2000)
                await hass.async_block_till_done()
                assert not observer.available
                # Another car follows before the disk works again: its cycle is
                # decided at once, and polls in between do not hammer the disk.
                coordinator.owner = SimpleNamespace(id="b" * 32)
                coordinator.complete(at=now - 1000)
                await hass.async_block_till_done()
                asked = saves.await_count
                for _ in range(5):
                    coordinator.async_update_listeners()
                    await hass.async_block_till_done()
                assert saves.await_count == asked <= 2
            assert observer._cycle.unplugged_at == now - 1000
            assert observer._assignable is None
            assert observer._booking.charge == charge
            observer._last_storage_attempt = float("-inf")
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer.available
            assert observer._booking is None
            assert observer.statistics.status == "waiting_for_vehicle"
            # A plug-in without charging changes nothing, and revives nothing
            # that another car's cycle made unassignable.
            coordinator.owner = mine
            coordinator.meter.observe(now - 500, coordinator.total)
            coordinator.meter.observe(now - 100, coordinator.total)
            coordinator.cycle(now - 400, now - 200)
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer._cycle.plugged_at == now - 400
            assert observer._assignable is None
            # A save whose outcome stayed open is repeated, also with nothing new to book.
            observer._storage_ok, observer._last_storage_attempt = False, float("-inf")
            coordinator.async_update_listeners()
            await hass.async_block_till_done()
            assert observer.available
        finally:
            named.stop()
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())
