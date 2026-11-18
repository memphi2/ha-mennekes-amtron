"""Changing HA prices, partial observations and tariff-independent saved charges."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.mennekes_amtron_rtu.cost_observer import (
    ChargingCostObserver,
    LastChargingCost,
    electricity_price,
)
from custom_components.mennekes_amtron_rtu.device.charging_cost import CostResult
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
        observer = ChargingCostObserver(hass, configured)
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
        observer = ChargingCostObserver(hass, configured)
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


def data(at, energy, *, present=False, fresh=True):
    return WallboxData(
        values={ENERGY_TOTAL.key: energy, EVSE_STATE.key: EvseState.CHARGING if present else EvseState.IDLE, CP_STATE.key: CpState.C2 if present else CpState.A1},
        energy_read=fresh, energy_observed_at=at,
    )


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


def test_dynamic_price_changes_applied_to_intervals_not_final_total(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.states.async_set("sensor.price", "0.20", {"unit_of_measurement": "EUR/kWh"})
        observer = ChargingCostObserver(hass, entry(dynamic=True))
        at = time.time()+1
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
                await observer.async_observe(data(at+100, 110))
            assert observer.last.result.cost == pytest.approx(3)
            assert observer.last.result.energy_kwh == 10
        finally:
            await observer.async_stop()
            assert observer._unsubscribe is None
            await hass.async_stop()
    asyncio.run(run())


def test_price_expiry_uses_reports_and_missing_data_marks_interval_unknown(tmp_path):
    async def run():
        from datetime import UTC, datetime

        from custom_components.mennekes_amtron_rtu.cost_observer import (
            PRICE_VALID_SECONDS,
        )
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(dynamic=True))
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


def test_profile_handoff_keeps_complete_cost_and_accepts_later_tariff_update(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        configured = entry()
        configured.options["active_vehicle_profile"] = "first"
        observer = ChargingCostObserver(hass, configured)
        at = time.time() + 1
        try:
            await observer.async_start()
            for offset, energy, present in ((0, 100, False), (10, 102, True)):
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + offset):
                    await observer.async_observe(data(at + offset, energy, present=present))
            assert observer.current.complete
            configured.options = {**configured.options, "active_vehicle_profile": "second"}
            assert observer.retain_for_reload(configured)
            observer.rebind(configured)
            for offset, energy, present in ((50, 105, True), (100, 110, False)):
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + offset):
                    await observer.async_observe(data(at + offset, energy, present=present))
            assert observer.last.result.cost == pytest.approx(3)
            assert observer.last.result.complete
            # The rebinding updates its configuration baseline, so a later
            # tariff-only update does not mistake the profile for another edit.
            configured.options = {**configured.options, "charging_network_tariff": {
                "module": "module_2", "regular_network": 0.1, "price_includes_network": True,
            }}
            assert observer.apply_tariff_only_update()
            assert observer.last.result.cost == pytest.approx(3)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    "closed", "corrupt", "entry", "connection", "serial", "layout",
    "source", "price", "entity", "tariff",
])
def test_handoff_rejects_changed_sources_and_connection(tmp_path, change):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        configured = entry()
        configured.options["charging_network_tariff"] = {"module": "none"}
        observer = ChargingCostObserver(hass, configured)
        candidate = entry()
        candidate.options = {**configured.options}
        if change == "closed":
            await observer.async_stop()
        elif change == "corrupt":
            observer._storage_corrupt = True
        elif change == "entry":
            candidate.entry_id = "different"
        elif change == "connection":
            candidate.data = {"host": "changed.invalid"}
        elif change == "serial":
            candidate.runtime_data.identity.serial_number = "different"
        elif change == "layout":
            candidate.runtime_data.identity.layout_version = 0x0102
        else:
            key, value = {
                "source": ("charging_cost_source", "disabled"),
                "price": ("charging_price", 0.4),
                "entity": ("charging_price_entity", "sensor.changed_price"),
                "tariff": ("charging_network_tariff", {
                    "module": "module_2", "regular_network": 0.1, "price_includes_network": True,
                }),
            }[change]
            candidate.options[key] = value
        try:
            assert not observer.retain_for_reload(candidate)
            with pytest.raises(ValueError, match="source changed"):
                observer.rebind(candidate)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_dynamic_price_reports_during_handoff_are_kept_in_complete_cost(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.states.async_set("sensor.price", "0.20", {"unit_of_measurement": "EUR/kWh"})
        configured = entry(dynamic=True)
        observer = ChargingCostObserver(hass, configured)
        at = time.time() + 1
        try:
            await observer.async_start()
            initial_subscription = observer._unsubscribe
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at):
                await observer.async_observe(data(at, 100))
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 25):
                await observer.async_observe(data(at + 25, 102.5, present=True))
            configured.options = {**configured.options, "active_vehicle_profile": "second"}
            assert observer.retain_for_reload(configured)
            # Polling has stopped for the logical reload. Price subscriptions
            # stay live so the next interval still uses the observed boundary.
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 50):
                hass.states.async_set("sensor.price", "0.40", {"unit_of_measurement": "EUR/kWh"})
                await hass.async_block_till_done()
            assert observer._price() == 0.4
            observer.rebind(configured)
            assert observer._unsubscribe is initial_subscription
            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 100):
                await observer.async_observe(data(at + 100, 110))
            assert observer.last.result.complete
            assert observer.last.result.cost == pytest.approx(3)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_one_failed_poll_and_plug_transition_keep_the_cost_complete(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry())
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            with patch(clock, return_value=at):
                await observer.async_observe(data(at, 100))
            with patch(clock, return_value=at+5):
                transition = data(at+5, 100, fresh=False)
                transition.values[CP_STATE.key] = CpState.B1  # CP leads the EVSE state
                await observer.async_observe(transition)
            with patch(clock, return_value=at+25):
                await observer.async_observe(data(at+25, 102.5, present=True))
                await observer.async_observe(None)
            with patch(clock, return_value=at+100):
                await observer.async_observe(data(at+100, 110))
            assert observer.last.result.cost == pytest.approx(3)
            assert observer.last.result.complete
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_session_published_after_the_next_idle_reading_keeps_its_cost(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry())
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            for offset, energy, present in (
                (0, 100, False),
                (25, 102.5, True),
                (90, 110, True),
                (100, 110, False),  # HA first sees the meter read only once unplugged.
                (160, 110, False),
            ):
                with patch(clock, return_value=at + offset):
                    await observer.async_observe(data(at + offset, energy, present=present))
            assert observer.last.result.complete
            assert observer.last.result.cost == pytest.approx(3)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_tariff_change_while_the_final_meter_is_pending_marks_a_gap(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        configured = entry()
        observer = ChargingCostObserver(hass, configured)
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            for offset, energy, present, fresh in ((0, 100, False, True), (25, 105, True, True), (100, 110, False, False)):
                with patch(clock, return_value=at + offset):
                    await observer.async_observe(data(at + offset, energy, present=present, fresh=fresh))
            configured.options = {**configured.options, "charging_network_tariff": {
                "module": "module_2", "regular_network": 0.1, "price_includes_network": True}}
            assert observer.apply_tariff_only_update()
            with patch(clock, return_value=at+160):
                await observer.async_observe(data(at+160, 110))
            assert not observer.last.result.complete
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_long_outage_keeps_the_running_cost_without_claiming_completeness(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry())
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            for offset, value in ((0, data(at, 100)), (10, data(at+10, 101, present=True)), (60, None), (200, None),
                                  (210, data(at+210, 105, present=True)), (270, data(at+270, 106, present=True))):
                with patch(clock, return_value=at + offset):
                    await observer.async_observe(value)
            assert not observer.current.complete
            assert observer.current.energy_kwh == 6
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_an_idle_poll_without_a_meter_read_keeps_the_running_cost_baseline(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry())
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            for offset, value in ((0, data(at, 100)), (10, data(at+10, 100, fresh=False)),
                                  (20, data(at+20, 105, present=True))):
                with patch(clock, return_value=at + offset):
                    await observer.async_observe(value)
            assert observer.current.complete
            assert observer.current.energy_kwh == 5
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_waiting_plugged_in_moves_the_running_cost_start_past_a_tariff_edit(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        configured = entry()
        observer = ChargingCostObserver(hass, configured)
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            with patch(clock, return_value=at):
                await observer.async_observe(data(at, 100))
            waiting = data(at+10, 100)
            waiting.values[EVSE_STATE.key], waiting.values[CP_STATE.key] = EvseState.EV_CONNECTED, CpState.B1
            with patch(clock, return_value=at+10):
                await observer.async_observe(waiting)
            configured.options = {**configured.options, "charging_network_tariff": {
                "module": "module_2", "regular_network": 0.1, "price_includes_network": True}}
            with patch(clock, return_value=at+20):
                assert observer.apply_tariff_only_update()
            for offset, energy in ((30, 100), (40, 105)):
                reading = data(at + offset, energy, present=energy > 100)
                if energy == 100:
                    reading.values[EVSE_STATE.key], reading.values[CP_STATE.key] = EvseState.EV_CONNECTED, CpState.B1
                with patch(clock, return_value=at + offset):
                    await observer.async_observe(reading)
            assert observer.current.complete
            assert observer.current.energy_kwh == 5
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_a_failed_meter_read_after_unplug_does_not_start_the_next_cost_mid_charge(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry())
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            for offset, value in ((0, data(at, 100)), (10, data(at+10, 105, present=True)),
                                  (20, data(at+20, None)),  # read failed, new sample time
                                  (30, data(at+30, 110, present=True))):
                with patch(clock, return_value=at + offset):
                    await observer.async_observe(value)
            assert not observer.current.complete
            assert observer.current.energy_kwh == 0
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("seen_unplugged", [True, False])
def test_replug_before_a_meter_read_starts_a_complete_running_cost(tmp_path, seen_unplugged):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry())
        at = time.time()+1
        try:
            await observer.async_start()
            clock = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"
            readings = [data(at, 100), data(at+10, 105, present=True)]
            if seen_unplugged:
                readings.append(data(at+20, 105, fresh=False))  # unplugged, meter not read
            waiting = data(at+30, 108)
            waiting.values[EVSE_STATE.key], waiting.values[CP_STATE.key] = EvseState.EV_CONNECTED, CpState.B1
            readings += [waiting, data(at+40, 110, present=True)]
            for value in readings:
                with patch(clock, return_value=value.energy_observed_at):
                    await observer.async_observe(value)
            # Without the unplug poll it is one cycle from the first idle reading.
            assert observer.current.complete
            assert observer.current.energy_kwh == (2 if seen_unplugged else 10)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


CLOCK = "custom_components.mennekes_amtron_rtu.cost_observer.time.time"


def reading(at, energy, state, *, fresh=True):
    """A poll: the car is away ("idle"), waiting ("plugged") or charging."""
    evse, cp = {
        "idle": (EvseState.IDLE, CpState.A1), "plugged": (EvseState.EV_CONNECTED, CpState.B1),
        "charging": (EvseState.CHARGING, CpState.C2),
    }[state]
    return WallboxData(
        values={ENERGY_TOTAL.key: energy, EVSE_STATE.key: evse, CP_STATE.key: cp},
        energy_read=fresh, energy_observed_at=at,
    )


async def play(observer, at, steps):
    for offset, energy, state, *rest in steps:
        with patch(CLOCK, return_value=at + offset):
            await observer.async_observe(
                None if state is None else reading(at + offset, energy, state, fresh=not rest or rest[0])
            )


def observed(tmp_path, body, *, dynamic=False):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        observer = ChargingCostObserver(hass, entry(dynamic=dynamic))
        try:
            await observer.async_start()
            await body(hass, observer, time.time() + 1)
        finally:
            await observer.async_stop()
            await hass.async_stop()
    asyncio.run(run())


def test_last_cost_storage_validation_and_roundtrip():
    last = LastChargingCost()
    assert LastChargingCost.restore(last.snapshot()).snapshot() == last.snapshot()
    last = LastChargingCost(CostResult(3, 0, 10, True))
    assert LastChargingCost.restore(last.snapshot()).result.cost == 3
    for raw in (None, {}, {"result": None, "session": None}, {"result": {"known_cost": 1}}):
        with pytest.raises(ValueError, match="cost"):
            LastChargingCost.restore(raw)


def test_a_charge_runs_from_zero_at_the_plug_in_and_becomes_the_last_charge(tmp_path):
    async def body(hass, observer, at):
        zero = CostResult(0, 0, 0, True)
        assert observer.current == zero
        assert observer.last.result is None
        await play(observer, at, [(0, 100, "idle"), (10, 100, "plugged")])
        # Plugged in: zero, and nothing to take over yet.
        assert observer.current == zero
        assert observer.last.result is None
        await play(observer, at, [(40, 102, "charging")])
        assert observer.current.cost == pytest.approx(0.6)
        # Still charging: the last charge is untouched.
        assert observer.last.result is None
        await play(observer, at, [(70, 110, "charging"), (100, 110, "idle")])
        # Unplugged: the running cost is zero again, its value is the last charge.
        assert observer.current == zero
        assert observer.last.result.cost == pytest.approx(3)
        assert observer.last.result.energy_kwh == pytest.approx(10)
        assert observer.last.result.complete
        await observer.async_stop()
        changed = entry()
        changed.options["charging_price"] = 2.0
        replacement = ChargingCostObserver(hass, changed)
        await replacement.async_start()
        # A saved charge is never repriced.
        assert replacement.last.result.cost == pytest.approx(3)
        await replacement.async_stop()
    observed(tmp_path, body)


def test_a_stop_while_plugged_in_takes_the_value_over_and_a_resume_runs_on(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [(0, 100, "idle"), (10, 100, "plugged"), (40, 104, "charging"), (70, 104, "plugged")])
        assert observer.last.result.cost == pytest.approx(1.2)
        assert observer.current.cost == pytest.approx(1.2)
        await play(observer, at, [(100, 106, "charging")])
        # Running on: the last charge keeps the value of the stop.
        assert observer.current.cost == pytest.approx(1.8)
        assert observer.last.result.cost == pytest.approx(1.2)
        await play(observer, at, [(130, 106, "idle")])
        assert observer.last.result.cost == pytest.approx(1.8)
        assert observer.current.energy_kwh == 0
    observed(tmp_path, body)


def test_a_plug_in_without_charging_keeps_the_last_charge(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [(0, 100, "idle"), (10, 100, "charging"), (40, 105, "charging"), (70, 105, "idle")])
        first = observer.last.result
        assert first.cost == pytest.approx(1.5)
        await play(observer, at, [(100, 105, "plugged"), (130, 105, "plugged"), (160, 105, "idle")])
        assert observer.last.result is first
    observed(tmp_path, body)


def test_the_final_meter_reading_after_the_unplug_completes_the_last_charge(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [
            (0, 100, "idle"), (10, 100, "charging"), (40, 105, "charging"),
            # The unplug poll comes before the meter was read again.
            (70, 105, "idle", False),
        ])
        assert observer.last.result.cost == pytest.approx(1.5)
        assert observer.current.energy_kwh == 0
        await play(observer, at, [(100, 106, "idle")])
        assert observer.last.result.cost == pytest.approx(1.8)
        assert observer.last.result.complete
        # That reading is the start of the next charge, nothing carries over.
        await play(observer, at, [(130, 106, "plugged"), (160, 107, "charging")])
        assert observer.current.cost == pytest.approx(0.3)
        assert observer.last.result.cost == pytest.approx(1.8)
    observed(tmp_path, body)


def test_an_unplug_without_a_meter_reading_does_not_leak_into_the_next_charge(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [(0, 100, "idle"), (10, 105, "charging"), (100, 105, "idle", False)])
        assert observer.last.result.cost == pytest.approx(1.5)
        # Another car plugs in before an idle meter reading arrives.
        await play(observer, at, [(105, 105, "charging", False)])
        assert observer.current.energy_kwh == 0
        await play(observer, at, [(110, 110, "charging"), (120, 111, "charging")])
        # Its first reading cannot stand in for the missing idle baseline.
        assert not observer.current.complete
        assert observer.current.energy_kwh == pytest.approx(1)
        assert observer.last.result.cost == pytest.approx(1.5)
    observed(tmp_path, body)


def test_a_charge_without_a_price_is_incomplete_and_shows_no_amount(tmp_path):
    async def body(_hass, observer, at):
        # No price entity state at all.
        await play(observer, at, [(0, 100, "idle"), (10, 100, "charging"), (60, 104, "charging"), (100, 104, "idle")])
        last = observer.last.result
        assert not last.complete
        assert last.unpriced_kwh == pytest.approx(4)
        assert last.cost is None
        assert last.estimate is None
    observed(tmp_path, body, dynamic=True)


def test_an_old_cached_reading_is_no_baseline(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [(-100, 100, "idle")])
        with patch(CLOCK, return_value=at):
            await observer.async_observe(reading(at, 101, "charging"))
        assert not observer.current.complete
        await play(observer, at, [(60, 110, "charging"), (100, 110, "idle")])
        # Only the observed part is priced, and it says so.
        assert observer.last.result.energy_kwh == pytest.approx(9)
        assert not observer.last.result.complete
        assert observer.last.result.estimate == pytest.approx(2.7)
    observed(tmp_path, body)


def test_a_long_outage_during_a_charge_keeps_its_cost_but_not_as_complete(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [
            (0, 100, "idle"), (25, 102.5, "charging"),
            (60, 0, None), (120, 0, None), (180, 0, None), (240, 0, None),
            (300, 110, "idle"),
        ])
        # Another car could have charged unseen: the amount stands, flagged.
        assert not observer.last.result.complete
        assert observer.last.result.estimate == pytest.approx(3)
    observed(tmp_path, body)


def test_a_long_gap_after_the_unplug_drops_the_pending_final_reading(tmp_path):
    async def body(_hass, observer, at):
        await play(observer, at, [(0, 100, "idle"), (10, 100, "charging"), (40, 105, "charging"), (70, 105, "idle", False)])
        unknown = reading(at + 80, 105, "idle", fresh=False)
        unknown.values[EVSE_STATE.key] = unknown.values[CP_STATE.key] = None
        with patch(CLOCK, return_value=at + 80):
            await observer.async_observe(unknown)
        with patch(CLOCK, return_value=at + 4000):
            await observer.async_observe(unknown)
        # Another car may have charged meanwhile: the old charge stays as it was.
        await play(observer, at, [(4010, 130, "idle")])
        assert observer.last.result.cost == pytest.approx(1.5)
    observed(tmp_path, body)


def test_failed_optional_storage_does_not_break_observation_or_unload(tmp_path):
    async def body(hass, observer, at):
        with patch.object(observer._store, "async_save", side_effect=OSError):
            await play(observer, at, [(0, 100, "idle"), (10, 100, "charging"), (60, 110, "charging"), (100, 110, "idle")])
            assert observer._dirty
            await observer._save()
        await observer.async_stop()
        assert not observer._dirty
        broken = ChargingCostObserver(hass, entry())
        with patch.object(broken._store, "async_load", side_effect=OSError):
            await broken.async_start()
            assert not broken.available
            await broken.async_observe(reading(at, 100, "idle"))
            await broken.async_stop()
    observed(tmp_path, body)


def test_an_unusable_saved_estimate_is_dropped_and_replaced(tmp_path):
    async def body(hass, observer, at):
        await observer.async_stop()
        observer._store.path.parent.mkdir(parents=True, exist_ok=True)
        observer._store.path.write_text('{"version": 1, "data": {"identity": "123", "goal": {"session": null, "result": null}}}')
        replacement = ChargingCostObserver(hass, entry())
        await replacement.async_start()
        assert replacement.last.result is None
        await play(replacement, at, [(0, 100, "idle"), (10, 100, "charging"), (60, 110, "charging"), (100, 110, "idle")])
        assert replacement.available
        await replacement.async_stop()
        again = ChargingCostObserver(hass, entry())
        await again.async_start()
        assert again.last.result.cost == pytest.approx(3)
        await again.async_stop()
    observed(tmp_path, body)
