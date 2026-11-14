"""Optional loss estimates do not own controls or guess historical car sessions."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

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
from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.registers import CP_STATE, EVSE_STATE
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.history import (
    HistoryMatch,
)
from tests.test_bavarian_history import VIN, vehicle_registry


class Coordinator:
    def __init__(self):
        self.data = WallboxData(values={EVSE_STATE.key: EvseState.IDLE, CP_STATE.key: CpState.A1}, session_summary_supported=True)
        self.last_update_success = True
        self.listeners = []

    def current_data(self):
        return self.data

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def async_update_listeners(self):
        for listener in tuple(self.listeners):
            listener()

    def complete(self, *, at=None, energy=10):
        at = time.time() if at is None else at
        session = CompletedSession(str(uuid4()), at - 100, at, at - 95, at - 90, at - 10, at - 5, energy)
        self.data.charging_session = session.as_dict()
        self.async_update_listeners()
        return session


def build(hass, mode="sensor", *, auto=False):
    if mode == "vehicle_history":
        hass.data.setdefault(dr.DATA_REGISTRY, vehicle_registry())
    coordinator = Coordinator()
    entry = SimpleNamespace(
        entry_id="entry", options={
            CONF_CHARGE_LOSS_SOURCE: mode, CONF_LOSS_AUTO_MATCH: auto,
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_energy",
            "charge_loss_vehicle_provider": "bavariandata",
            "charge_loss_vehicle_device": "car-device",
        }, runtime_data=SimpleNamespace(
            coordinator=coordinator, identity=SimpleNamespace(serial_number="123", layout_version=0x0103),
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


@pytest.mark.parametrize("mode", ["vehicle_history"])
def test_history_uses_public_adapter_retries_and_does_not_call_on_each_poll(tmp_path, mode):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, mode)
        callbacks = []
        cancellations = []
        def schedule(_hass, delay, callback):
            callbacks.append((delay, callback))
            return lambda: cancellations.append(delay)
        with patch("custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history", new=AsyncMock(side_effect=[TimeoutError, HomeAssistantError(), None, HistoryMatch(8, 2)])) as read, patch("custom_components.mennekes_amtron_rtu.charge_loss_observer.async_call_later", side_effect=schedule):
            try:
                await observer.async_start()
                read.assert_not_awaited()
                coordinator.complete()
                await hass.async_block_till_done()
                assert callbacks[0][0] == 60
                observer._schedule_retry()
                assert len(callbacks) == 1
                for expected in (300, 1800):
                    callbacks[-1][1](None)
                    await hass.async_block_till_done()
                    assert callbacks[-1][0] == expected
                callbacks[-1][1](None)
                await hass.async_block_till_done()
                assert observer.statistics.last_loss_percent == pytest.approx(20)
                assert observer.statistics.association == "history"
                observer._schedule_retry()
                assert len(callbacks) == 3
                coordinator.async_update_listeners()
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert read.await_count == 4
                observer._last_query = float("-inf")
                read.side_effect = None
                read.return_value = HistoryMatch(9, 1)
                hass.bus.async_fire("bavariandata_charging_stopped", {})
                await hass.async_block_till_done()
                assert observer.statistics.last_loss_percent == pytest.approx(10)
                assert observer.statistics.sample_count == 1
                coordinator.complete(at=time.time()+200)
                read.return_value = None
                await hass.async_block_till_done()
                observer._retry = lambda: cancellations.append(1800)
                coordinator.complete(at=time.time()+400)
                await hass.async_block_till_done()
                assert cancellations
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
            patch(
                "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history",
                new=AsyncMock(side_effect=[ValueError("private vehicle data"), HistoryMatch(8, 1)]),
            ),
            patch("custom_components.mennekes_amtron_rtu.charge_loss_observer.async_call_later", side_effect=schedule),
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
            with patch.object(broken._store, "async_load", side_effect=ValueError):
                await broken.async_start()
                assert not broken.available
                coord2.complete()
                await hass.async_block_till_done()
                assert broken._task is None
                await broken.async_stop()
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


@pytest.mark.parametrize("failed_attempts", [1, 2])
def test_history_match_is_retried_after_storage_recovers_without_poll_churn(tmp_path, failed_attempts):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        with patch(
            "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history",
            new=AsyncMock(return_value=HistoryMatch(8, 1)),
        ) as read:
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

                with patch.object(observer._store, "async_save", wraps=observer._store.async_save) as save:
                    for _ in range(5):
                        coordinator.async_update_listeners()
                        await hass.async_block_till_done()
                    save.assert_not_awaited()
                    assert read.await_count == failed_attempts + 1
            finally:
                await observer.async_stop()
                await hass.async_stop()

    asyncio.run(run())


def test_old_history_response_is_discarded_and_inflight_query_cancels_on_unload(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "vehicle_history")
        started, release = asyncio.Event(), asyncio.Event()
        async def read(*_args, **_kwargs):
            started.set()
            await release.wait()
            return HistoryMatch(8, 1)
        with patch("custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history", side_effect=read):
            try:
                await observer.async_start()
                coordinator.complete()
                await started.wait()
                coordinator.data.charging_session = None
                coordinator.async_update_listeners()
                release.set()
                await hass.async_block_till_done()
                assert observer.statistics.sample_count == 0
                assert observer.statistics.last_loss_percent is None
                release.clear()
                started.clear()
                coordinator.complete(at=time.time()+300)
                await started.wait()
                await observer.async_stop()
                assert observer._task.done()
            finally:
                await observer.async_stop()
                await hass.async_stop()
    asyncio.run(run())


def test_account_or_rebound_device_is_unavailable_and_never_matches_a_different_car(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "account-entry")})
        observer, coordinator = build(hass, "vehicle_history")
        with patch("custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history", new=AsyncMock()) as read:
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
            with patch(
                "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history",
                return_value=HistoryMatch(8, 1),
            ):
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
        with patch(
            "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history",
            return_value=HistoryMatch(8, 1),
        ) as read:
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
            with patch(
                "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.async_read_history",
            ) as read:
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
