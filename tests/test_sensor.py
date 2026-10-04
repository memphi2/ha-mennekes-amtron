from __future__ import annotations

import asyncio

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.sensor import async_setup_entry
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
    assert sensors["evse_state"].native_value == "idle"
    assert sensors["cp_state"].native_value == "b1"
    assert sensors["authorization_status"].native_value == "authorized"
    assert sensors["cable_lock_status"].native_value == "fixed_cable"
    assert sensors["power_total"].native_value == 0.0
    assert sensors["voltage_l1"].native_value is not None
    assert sensors["sessions_total"].native_value == 42
    assert sensors["session_duration"].native_value == 600
    assert sensors["modbus_layout_version"].native_value == "v01.03"
    assert sensors["grid_phases_connected"].native_value == "l1_l2_l3"
    assert sensors["detected_ev_phases"].native_value == "three_phases"


def test_an_older_layout_gets_fewer_sensors() -> None:
    sensors = asyncio.run(_sensors(layout=R.LAYOUT_V01_00))
    assert "temperature" not in sensors
    assert "energy_total" not in sensors
    assert "signaled_current" not in sensors
    assert "evse_state" in sensors


def test_the_error_sensor_reports_a_category_and_the_raw_code() -> None:
    async def run() -> dict[str, object]:
        return await _sensors(bank=device_bank(error_code=200))

    sensors = asyncio.run(run())
    error = sensors["error_code"]
    assert error.native_value == "energy_manager_unavailable"
    assert error.extra_state_attributes == {"code": 200}


def test_an_undocumented_error_code_becomes_other() -> None:
    sensors = asyncio.run(_sensors(bank=device_bank(error_code=4711)))
    assert sensors["error_code"].native_value == "other"
    assert sensors["error_code"].extra_state_attributes == {"code": 4711}


def test_a_missing_value_yields_no_state() -> None:
    async def run() -> None:
        hass = FakeHass()
        _hass, entry, _transport = await build_runtime(hass=hass)
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
        entry.runtime_data.coordinator.data.values["evse_state"] = None
        entry.runtime_data.coordinator.data.values["power_total"] = None
        entry.runtime_data.coordinator.data.values["modbus_layout_version"] = None
        entry.runtime_data.coordinator.data.values["error_code"] = None
        assert added["evse_state"].native_value is None
        assert added["power_total"].native_value is None
        assert added["modbus_layout_version"].native_value is None
        assert added["error_code"].extra_state_attributes is None
        assert added["error_code"].native_value is None

    asyncio.run(run())


def test_an_undocumented_enum_value_yields_no_state() -> None:
    async def run() -> None:
        hass = FakeHass()
        _hass, entry, _transport = await build_runtime(hass=hass)
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
        entry.runtime_data.coordinator.data.values["evse_state"] = 42
        assert added["evse_state"].native_value is None

    asyncio.run(run())
