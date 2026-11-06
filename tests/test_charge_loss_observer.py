"""Optional loss estimates do not own controls or guess historical car sessions."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.bavarian_history import HistoryMatch
from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_AUTO_MATCH,
    CONF_LOSS_BAVARIAN_DEVICE,
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
    if mode == "bavariandata":
        hass.data.setdefault(dr.DATA_REGISTRY, vehicle_registry())
    coordinator = Coordinator()
    entry = SimpleNamespace(
        entry_id="entry", options={
            CONF_CHARGE_LOSS_SOURCE: mode, CONF_LOSS_AUTO_MATCH: auto,
            CONF_VEHICLE_LAST_CHARGE_ENERGY: "sensor.car_energy",
            CONF_LOSS_BAVARIAN_DEVICE: "car-device",
        }, runtime_data=SimpleNamespace(
            coordinator=coordinator, identity=SimpleNamespace(serial_number="123", layout_version=0x0103),
        ),
    )
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


def test_history_uses_public_adapter_retries_and_does_not_call_on_each_poll(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "bavariandata")
        callbacks = []
        cancellations = []
        def schedule(_hass, delay, callback):
            callbacks.append((delay, callback))
            return lambda: cancellations.append(delay)
        with patch("custom_components.mennekes_amtron_rtu.charge_loss_observer.async_read_history", new=AsyncMock(side_effect=[TimeoutError, HomeAssistantError(), None, HistoryMatch(8, 2)])) as read, patch("custom_components.mennekes_amtron_rtu.charge_loss_observer.async_call_later", side_effect=schedule):
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


def test_old_history_response_is_discarded_and_inflight_query_cancels_on_unload(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer, coordinator = build(hass, "bavariandata")
        started, release = asyncio.Event(), asyncio.Event()
        async def read(*_args, **_kwargs):
            started.set()
            await release.wait()
            return HistoryMatch(8, 1)
        with patch("custom_components.mennekes_amtron_rtu.charge_loss_observer.async_read_history", side_effect=read):
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
        observer, coordinator = build(hass, "bavariandata")
        with patch("custom_components.mennekes_amtron_rtu.charge_loss_observer.async_read_history", new=AsyncMock()) as read:
            try:
                await observer.async_start()
                coordinator.complete()
                await hass.async_block_till_done()
                assert not observer.available
                read.assert_not_awaited()
            finally:
                await observer.async_stop()
        hass.data[dr.DATA_REGISTRY] = vehicle_registry()
        observer, _coordinator = build(hass, "bavariandata")
        assert observer.available
        assert VIN not in observer._source
        hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
        assert not observer.available
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
