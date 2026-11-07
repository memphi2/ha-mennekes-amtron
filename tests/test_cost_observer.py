"""Changing HA prices, partial observations and tariff-independent saved charges."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.mennekes_amtron_rtu.cost_observer import (
    ChargingCostObserver,
    LastChargingCost,
    electricity_price,
)
from custom_components.mennekes_amtron_rtu.device.charging_cost import CostResult
from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.registers import (
    CP_STATE,
    ENERGY_TOTAL,
    EVSE_STATE,
)


def entry(*, dynamic=False):
    return SimpleNamespace(
        entry_id="entry", data={}, options={
            "charging_cost_source": "sensor" if dynamic else "fixed",
            "charging_price_entity": "sensor.price", "charging_price": 0.3,
        }, runtime_data=SimpleNamespace(identity=SimpleNamespace(serial_number="123", layout_version=0x0103)),
    )


def test_tariff_only_update_keeps_cost_observer_and_modbus_connection(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        configured = entry()
        observer = ChargingCostObserver(hass, configured, remote=False)
        original_accumulator = observer._accumulator
        configured.options = {
            **configured.options,
            "charging_network_tariff": {
                "module": "module_2", "regular_network": 0.1,
                "price_includes_network": True,
            },
        }
        assert observer.apply_tariff_only_update()
        assert observer._accumulator is original_accumulator
        assert observer._accumulator.tariff.module == "module_2"
        configured.options["charging_price"] = 0.4
        assert not observer.apply_tariff_only_update()
        await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["unchanged", "credentials", "connection_with_tariff"])
def test_only_actual_tariff_changes_with_unchanged_connection_can_skip_reload(tmp_path, change):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        configured = entry()
        observer = ChargingCostObserver(hass, configured, remote=True)
        if change != "unchanged":
            configured.data = {"server_token": "replacement-credential"}
        if change == "connection_with_tariff":
            configured.options = {
                **configured.options,
                "charging_network_tariff": {"module": "module_2", "regular_network": 0.1, "price_includes_network": True},
            }
        assert not observer.apply_tariff_only_update()
        assert observer._tariff.module == "none"
        await hass.async_stop()

    asyncio.run(run())


def data(at, energy, *, present=False, fresh=True, session=None):
    return WallboxData(
        values={ENERGY_TOTAL.key: energy, EVSE_STATE.key: EvseState.CHARGING if present else EvseState.IDLE, CP_STATE.key: CpState.C2 if present else CpState.A1},
        energy_read=fresh, energy_observed_at=at,
        charging_session=session.as_dict() if session else None,
        session_summary_supported=True,
    )


def completed(at, energy=10):
    return CompletedSession(str(uuid4()), at, at+100, at+5, at+10, at+90, at+95, energy)


@pytest.mark.parametrize(("value", "unit", "expected"), [
    ("0.35", "EUR/kWh", 0.35), ("-0.02", "€/kWh", -0.02),
    ("35", "ct/kWh", 0.35), ("350", "EUR/MWh", 0.35), ("350", "€/MWh", 0.35),
    ("0", "EUR/kWh", 0), ("unknown", "EUR/kWh", None),
    ("nan", "EUR/kWh", None), ("inf", "EUR/kWh", None),
    ("0.35", None, None), ("0.35", "USD/kWh", None),
])
def test_price_units_negative_rates_and_unknown(value, unit, expected):
    state = State("sensor.price", value, {"unit_of_measurement": unit})
    assert electricity_price(state) == pytest.approx(expected) if expected is not None else electricity_price(state) is None
    assert electricity_price(None) is None
    assert electricity_price(State("sensor.price", "0.3", {"unit_of_measurement": "EUR/kWh", "restored": True})) is None


def test_last_cost_storage_validation_and_roundtrip():
    last = LastChargingCost()
    assert LastChargingCost.restore(last.snapshot()).snapshot() == last.snapshot()
    last.session, last.result = completed(1000), CostResult(3, 0, 10, True)
    assert LastChargingCost.restore(last.snapshot()).result.cost == 3
    for bad in (None, {}, {"session": None, "result": last.result.as_dict()}, {"session": last.session.as_dict(), "result": CostResult(1, 0, 5, True).as_dict()}):
        with pytest.raises(ValueError, match="charging cost"):
            LastChargingCost.restore(bad)


@pytest.mark.parametrize("remote", [False, True])
def test_complete_fixed_price_cycle_survives_reload_without_repricing(tmp_path, remote):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(), remote=remote)
        at = time.time()+1
        session = completed(at)
        try:
            await observer.async_start()
            assert observer.current is None
            for offset, energy, present in ((0, 100, False), (10, 102, True), (50, 105, True), (100, 110, False)):
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+offset):
                    await observer.async_observe(data(at+offset, energy, present=present, session=session if offset == 100 else None))
                if present:
                    assert observer.current.complete
            assert observer.current is None
            assert observer.last.result.cost == pytest.approx(3)
            await observer.async_stop()
            changed = entry()
            changed.options["charging_price"] = 2.0
            replacement = ChargingCostObserver(hass, changed, remote=remote)
            await replacement.async_start()
            assert replacement.last.result.cost == pytest.approx(3)
            await replacement.async_stop()
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_dynamic_price_changes_applied_to_intervals_not_final_total(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.states.async_set("sensor.price", "0.20", {"unit_of_measurement": "EUR/kWh"})
        observer = ChargingCostObserver(hass, entry(dynamic=True), remote=False)
        at = time.time()+1
        session = completed(at)
        try:
            await observer.async_start()
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at):
                await observer.async_observe(data(at, 100))
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+25):
                await observer.async_observe(data(at+25, 102.5, present=True))
            # Explicit event time makes the tariff boundary independent of test speed.
            from datetime import UTC, datetime

            observer._price_changed(SimpleNamespace(data={"new_state": State("sensor.price", "0.40", {"unit_of_measurement": "EUR/kWh"})}, time_fired=datetime.fromtimestamp(at+50, UTC)))
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+100):
                await observer.async_observe(data(at+100, 110, session=session))
            assert observer.last.result.cost == pytest.approx(3)
            assert observer.last.result.energy_kwh == 10
        finally:
            await observer.async_stop()
            assert observer._unsubscribe is None
            await hass.async_stop()
    asyncio.run(run())


def test_gaps_restart_and_unknown_price_do_not_manufacture_full_cost(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(dynamic=True), remote=True)
        at = time.time()+1
        try:
            await observer.async_start()
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at):
                await observer.async_observe(data(at-100, 100))  # Old cache is no baseline.
                await observer.async_observe(data(at, 101, present=True))
                assert not observer.current.complete
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+100):
                await observer.async_observe(data(at+100, 110, session=completed(at)))
            assert observer.last.result.cost is None
            assert observer.last.result.unpriced_kwh == 10
            at += 200
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at):
                await observer.async_observe(data(at, 110))
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+10):
                await observer.async_observe(data(at+10, 111, present=True))
                await observer.async_observe(None)
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+100):
                await observer.async_observe(data(at+100, 120, session=completed(at)))
            assert observer.last.result.cost is None
            unknown = data(at+101, None)
            unknown.values[EVSE_STATE.key] = None
            unknown.values[CP_STATE.key] = None
            await observer.async_observe(unknown)
            assert observer.last.result is None
            await observer.async_stop()
            await observer.async_observe(data(at+200, 120))
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("remote", [False, True])
def test_unplug_without_fresh_meter_cannot_leak_cost_into_next_charge(tmp_path, remote):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(), remote=remote)
        at = time.time() + 1
        session = completed(at)
        try:
            await observer.async_start()
            for offset, energy, present, fresh, summary in (
                (0, 100, False, True, None),
                (10, 105, True, True, None),
                (100, 105, False, False, session),
            ):
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + offset):
                    await observer.async_observe(data(at + offset, energy, present=present, fresh=fresh, session=summary))
            assert observer.current is None
            assert observer.last.result is not None
            assert not observer.last.result.complete

            # Another plug cycle starts before an idle meter baseline arrives.
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 105):
                await observer.async_observe(data(at + 105, 105, present=True, fresh=False, session=session))
            assert observer.current is not None
            assert not observer.current.complete
            assert observer.current.energy_kwh == 0

            # The first observed value during that cycle cannot reconstruct its
            # missing idle baseline or count the preceding charge a second time.
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 110):
                await observer.async_observe(data(at + 110, 110, present=True, session=session))
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 120):
                await observer.async_observe(data(at + 120, 111, present=True, session=session))
            assert not observer.current.complete
            assert observer.current.energy_kwh == 1
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_failed_optional_storage_does_not_break_observation_or_unload(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(), remote=False)
        at = time.time()+1
        try:
            await observer.async_start()
            with patch.object(observer._store, "async_save", side_effect=OSError):
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at+100):
                    await observer.async_observe(data(at+100, 110, session=completed(at)))
                assert observer._dirty
                await observer._save()
            await observer.async_stop()
            assert not observer._dirty
            broken = ChargingCostObserver(hass, entry(), remote=False)
            with patch.object(broken._store, "async_load", side_effect=OSError):
                await broken.async_start()
                assert not broken.available
                await broken.async_observe(data(at, 100))
                await broken.async_stop()
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_price_expiry_uses_reports_and_missing_data_marks_interval_unknown(tmp_path):
    async def run():
        from datetime import UTC, datetime

        from custom_components.mennekes_amtron_rtu.cost_observer import (
            PRICE_VALID_SECONDS,
        )
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(dynamic=True), remote=False)
        at = time.time()+1
        callbacks = []
        def schedule(_hass, delay, callback):
            callbacks.append(callback)
            return lambda: None
        with patch("custom_components.mennekes_amtron_rtu.cost_observer.async_call_later", side_effect=schedule):
            try:
                await observer.async_start()
                old = State("sensor.price", "0.3", {"unit_of_measurement": "EUR/kWh"}, last_reported=datetime.fromtimestamp(at - PRICE_VALID_SECONDS - 1, UTC))
                observer._accept_price(old, at)
                assert observer._price() is None
                assert not callbacks
                fresh = State("sensor.price", "0.3", {"unit_of_measurement": "EUR/kWh"}, last_reported=datetime.fromtimestamp(at, UTC))
                observer._accept_price(fresh, at)
                assert observer._price() == 0.3
                observer._accept_price(fresh, at+1)
                assert observer._price_at == at
                callbacks[-1](None)
                assert observer._price() is None
                assert observer._price_at == pytest.approx(at + PRICE_VALID_SECONDS, abs=1e-6, rel=0)
                hass.states.async_set("sensor.price", "0.4", {"unit_of_measurement": "EUR/kWh"})
                await hass.async_block_till_done()
                hass.states.async_set("sensor.price", "0.4", {"unit_of_measurement": "EUR/kWh"})
                await hass.async_block_till_done()
                assert observer._price() == 0.4
                hass.states.async_remove("sensor.price")
                await hass.async_block_till_done()
                assert observer._price() is None
            finally:
                await observer.async_stop()
                await hass.async_stop()
    asyncio.run(run())
