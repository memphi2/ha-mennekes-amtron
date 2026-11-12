from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.sensor import async_setup_entry
from tests.fakes import device_bank
from tests.ha_fakes import FakeHass, build_runtime, collect


async def _sensors(**kwargs: object) -> dict[str, object]:
    hass, entry, _transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}

    def add(entities: object) -> None:
        added.update(collect(entities))

    await async_setup_entry(hass, entry, add)
    return added


def test_the_validated_device_exposes_every_sensor() -> None:
    sensors = asyncio.run(_sensors())
    assert sensors["authorization_status"].native_value == "authorized"
    assert sensors["authorization_status"].extra_state_attributes == {"enabled": False}
    assert sensors["cable_lock_status"].native_value == "fixed_cable"
    assert sensors["cable_lock_status"].extra_state_attributes == {"permanent": False}
    assert sensors["power_total"].native_value == 0.0
    assert sensors["detected_ev_phases"].native_value == "three_phases"
    assert sensors["wallbox_settings"].native_value == "v01.03"
    settings = sensors["wallbox_settings"].extra_state_attributes
    assert settings["grid_phases_connected"] == "l1_l2_l3"
    assert settings["phase_options"] == "one_or_three_phases"
    assert settings["grid_imbalance_monitoring"] is True
    assert settings["max_evse_current"] == 16.0


def test_the_voltages_ride_on_the_total_power_only_under_load() -> None:
    sensors = asyncio.run(_sensors())
    power = sensors["power_total"]
    values = power.coordinator.data.values
    # Idle: a wobbling mains voltage would write a row every poll for nothing.
    assert power.extra_state_attributes is None
    values["power_total"] = 3680.0
    values["voltage_l2"] = 229.4
    values["power_l1"] = 1226.6
    assert power.extra_state_attributes == {
        "current_l1": 0.0, "current_l2": 0.0, "current_l3": 0.0,
        "voltage_l1": 230, "voltage_l2": 229, "voltage_l3": 230,
        "power_l1": 1227, "power_l2": 0, "power_l3": 0,
    }
    values["power_total"] = True
    assert power.extra_state_attributes is None


def test_the_session_counters_ride_on_the_session_energy() -> None:
    sensors = asyncio.run(_sensors())
    energy = sensors["session_energy"]
    assert energy.extra_state_attributes == {
        "duration_minutes": 10, "maximum_current": 16.0, "sessions": 42,
    }


def test_an_older_layout_gets_fewer_sensors_and_attributes() -> None:
    sensors = asyncio.run(_sensors(layout=R.LAYOUT_V01_00))
    assert "temperature" not in sensors
    assert "energy_total" not in sensors
    assert "signaled_current" not in sensors
    assert "wallbox_settings" in sensors
    assert "grid_phases_connected" not in (sensors["wallbox_settings"].extra_state_attributes or {})
    assert sensors["authorization_status"].extra_state_attributes is None


def test_a_missing_value_yields_no_state() -> None:
    async def run() -> None:
        hass = FakeHass()
        _hass, entry, _transport = await build_runtime(hass=hass)
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
        values = entry.runtime_data.coordinator.data.values
        values["power_total"] = 1000.0
        for key in [f"{kind}_l{n}" for kind in ("current", "voltage", "power") for n in (1, 2, 3)]:
            values[key] = None
        assert added["power_total"].extra_state_attributes is None
        values["power_total"] = None
        values["modbus_layout_version"] = None
        values["authorization_status"] = None
        assert added["power_total"].native_value is None
        assert added["power_total"].extra_state_attributes is None
        assert added["wallbox_settings"].native_value is None
        assert added["authorization_status"].native_value is None

    asyncio.run(run())


def test_an_undocumented_enum_value_yields_no_state() -> None:
    async def run() -> None:
        hass = FakeHass()
        _hass, entry, _transport = await build_runtime(hass=hass)
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
        entry.runtime_data.coordinator.data.values["authorization_status"] = 42
        entry.runtime_data.coordinator.data.values["phase_rotation"] = 42
        assert added["authorization_status"].native_value is None
        assert "phase_rotation" not in added["wallbox_settings"].extra_state_attributes

    asyncio.run(run())


def test_a_bool_or_text_never_becomes_an_attribute_number() -> None:
    async def run() -> None:
        sensors = await _sensors(bank=device_bank())
        values = sensors["power_total"].coordinator.data.values
        values["power_total"] = 1000.0
        values["voltage_l1"] = True
        values["voltage_l2"] = "230"
        values["session_duration"] = -5
        values["sessions_total"] = True
        assert sensors["power_total"].extra_state_attributes == {
            "current_l1": 0.0, "current_l2": 0.0, "current_l3": 0.0,
            "voltage_l3": 230, "power_l1": 0, "power_l2": 0, "power_l3": 0,
        }
        assert sensors["session_energy"].extra_state_attributes == {"maximum_current": 16.0}

    asyncio.run(run())
