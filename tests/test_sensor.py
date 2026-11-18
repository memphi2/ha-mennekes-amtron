from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
)
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
)
from custom_components.mennekes_amtron_rtu.device.enums import (
    DowngradeStatus,
    EvseState,
)
from custom_components.mennekes_amtron_rtu.sensor import _enum, async_setup_entry
from custom_components.mennekes_amtron_rtu.service_control import ServiceHeartbeat
from tests.fakes import device_bank
from tests.ha_fakes import (
    FakeConfigEntry,
    FakeHass,
    build_runtime,
    collect,
    heartbeat_running,
)


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


def test_heartbeat_health_rides_on_connection_quality_and_writes_only_transitions() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        client = entry.runtime_data.client
        heartbeat = ServiceHeartbeat(client)
        entry.runtime_data.heartbeat = heartbeat
        sensors: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: sensors.update(collect(items)))
        quality = sensors["connection_quality"]
        quality.hass = hass
        writes: list[object] = []
        quality.async_write_ha_state = lambda: writes.append(quality.extra_state_attributes)
        await quality.async_added_to_hass()
        assert quality.native_value == "good"
        assert quality.extra_state_attributes == {"heartbeat": "problem"}

        client.snapshot["heartbeat"] = {"running": True, "healthy": True}
        # A hundred acknowledged beats change nothing worth a state.
        for _ in range(100):
            heartbeat.update()
        assert writes == [{"heartbeat": "ok"}]
        assert heartbeat_running(heartbeat)

        # Read retries stay download diagnostics, never an attribute.
        for _ in range(10):
            entry.service.client.link_quality.observe_retry()
        await entry.runtime_data.coordinator.async_refresh()
        assert entry.runtime_data.coordinator.data.connection_retries == 10
        assert quality.extra_state_attributes == {"heartbeat": "ok"}

        client.snapshot["heartbeat"] = {"running": True, "healthy": False}
        heartbeat.update()
        client.snapshot["heartbeat"] = {"running": True, "healthy": True}
        heartbeat.update()
        assert writes == [{"heartbeat": "ok"}, {"heartbeat": "problem"}, {"heartbeat": "ok"}]

        # Removing the entity ends its subscription.
        for remove in quality._on_remove or ():
            remove()
        heartbeat.update()
        assert not heartbeat._listeners

    asyncio.run(run())


def test_without_a_heartbeat_the_connection_quality_reports_no_health() -> None:
    async def run() -> None:
        sensors = await _sensors()
        quality = sensors["connection_quality"]
        assert quality.extra_state_attributes is None

    asyncio.run(run())


def test_the_charging_status_names_the_wallbox_state_and_a_downgrade() -> None:
    async def run() -> None:
        sensors = await _sensors()
        status = sensors["charging_status"]
        values = status.coordinator.data.values
        assert status.native_value == status.coordinator.data.charging_display
        assert status.extra_state_attributes == {"evse_state": "idle", "cp_state": "b1"}
        values["downgrade_status"] = int(DowngradeStatus.DOWNGRADED)
        values["downgrade_current"] = 10.0
        assert status.extra_state_attributes["downgraded_to"] == 10.0
        # Without the service's own display, the registers decide.
        status.coordinator.data.charging_display = None
        values.update(evse_state=int(EvseState.CHARGING), cp_state=int(R.CpState.C2))
        assert status.native_value == "charging"

    asyncio.run(run())


def test_the_waiting_reason_carries_who_controls_and_what_comes_next() -> None:
    async def run() -> None:
        sensors = await _sensors()
        waiting = sensors["operation_waiting"]
        runtime = waiting.runtime
        assert waiting.available
        assert waiting.native_value == "none"
        assert waiting.extra_state_attributes == {
            "controller": "direct", "next_event": "none", "vehicle": "unknown",
            "pending": "none", "energy_target": "disabled",
        }
        runtime.vehicle_soc = SimpleNamespace(source="sensor.car_soc")
        runtime.timed_goal = SimpleNamespace(
            status="paused_until_start", planned_start="2026-11-16T03:00:00+00:00",
            forecast=SimpleNamespace(finish_at="2026-11-16T06:00:00+00:00", level_at_departure=80.0),
        )
        runtime.plug_charge = SimpleNamespace(status="blocked", blocked_reason="release_off")
        details = waiting.extra_state_attributes
        assert details["vehicle_target"] == "disabled"
        assert details["departure_plan"] == "paused_until_start"
        assert details["planned_start"] == "2026-11-16T03:00:00+00:00"
        assert details["estimated_finish"] == "2026-11-16T06:00:00+00:00"
        assert details["estimated_level_at_departure"] == 80.0
        assert details["plug_charge"] == "blocked"
        assert details["plug_charge_blocked"] == "release_off"
        runtime.timed_goal = SimpleNamespace(status="queued", planned_start=None, forecast=None)
        assert "estimated_finish" not in waiting.extra_state_attributes

        # A service without an operation summary leaves the reason unknown.
        runtime.client.operation = None
        assert waiting.native_value is None
        assert waiting.extra_state_attributes is None
        assert not waiting.available

    asyncio.run(run())


def _loss(available: bool = True) -> SimpleNamespace:
    statistics = SimpleNamespace(
        last_loss_percent=12.345, average_loss_percent=10.04, sample_count=4, association="vehicle",
    )
    return SimpleNamespace(statistics=statistics, available=available)


def test_the_charging_loss_exists_with_a_loss_source_and_reports_its_samples() -> None:
    async def run() -> None:
        assert "last_charging_loss" not in await _sensors()
        entry = FakeConfigEntry(options={CONF_CONTROL_MODE: CONTROL_MODE_MASTER, CONF_CHARGE_LOSS_SOURCE: "sensor"})
        sensors = await _sensors(entry=entry)
        last, average = sensors["last_charging_loss"], sensors["average_charging_loss"]
        assert not last.available
        assert last.native_value is None
        assert last.extra_state_attributes is None
        last.runtime.charge_loss = _loss()
        assert last.available
        assert average.available
        assert last.native_value == 12.3
        assert average.native_value == 10.0
        assert last.extra_state_attributes == {"sample_count": 4, "association": "vehicle"}
        assert average.extra_state_attributes == {"sample_count": 4}
        # A kept value of a finished charge stays while the wallbox is away.
        last.runtime.coordinator.last_update_success = False
        assert last.available
        assert average.available
        last.runtime.charge_loss = _loss(available=False)
        assert not last.available

    asyncio.run(run())


def test_the_estimated_cost_exists_with_a_price_and_reports_its_coverage() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        result = SimpleNamespace(estimate=1.2345, complete=False, energy_kwh=7.256, unpriced_kwh=0.004)
        cost = SimpleNamespace(available=True, current=result, last=SimpleNamespace(result=result))
        entry.runtime_data.charging_cost = cost
        sensors: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda items: sensors.update(collect(items)))
        current, last = sensors["current_estimated_charging_cost"], sensors["last_estimated_charging_cost"]
        assert not current.entity_registry_enabled_default
        assert current.available
        assert last.available
        assert current.native_value == 1.23
        assert current.extra_state_attributes == {"complete": False}
        assert last.extra_state_attributes == {"complete": False, "energy_kwh": 7.26, "unpriced_kwh": 0.0}
        # The last charge's cost does not go with a failed poll.
        entry.runtime_data.coordinator.last_update_success = False
        assert current.available
        assert last.available
        cost.current = None
        cost.last = SimpleNamespace(result=None)
        assert current.native_value is None
        assert last.native_value is None
        assert current.extra_state_attributes is None
        assert last.extra_state_attributes is None
        cost.available = False
        assert not current.available
        entry.runtime_data.charging_cost = None
        assert current.native_value is None
        assert last.extra_state_attributes is None
        assert not last.available

    asyncio.run(run())


def test_a_diagnostic_needs_named_values() -> None:
    with pytest.raises(ValueError, match="no named values"):
        _enum("temperature")


def test_one_failed_meter_read_leaves_no_gap_in_the_energy_total() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda entities: added.update(collect(entities)))
        energy = added["energy_total"]
        runtime = entry.runtime_data
        assert energy.native_value == pytest.approx(1234.5)
        assert runtime.client.last_energy_total == pytest.approx(1234.5)
        # The observation of a failed read carries no value: the last one stays.
        runtime.coordinator.data.values["energy_total"] = None
        assert energy.available
        assert energy.native_value == pytest.approx(1234.5)
        # Nothing was ever read: there is nothing to show.
        runtime.client.last_energy_total = None
        assert not energy.available
        assert energy.native_value is None
        await runtime.client.async_close()
        await entry.service.stop()

    asyncio.run(run())


def test_two_refreshes_never_fetch_at_the_same_time() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        coordinator = entry.runtime_data.coordinator
        fetch = coordinator._fetch
        running: list[int] = []
        most = 0

        async def counted():
            nonlocal most
            running.append(1)
            most = max(most, len(running))
            await asyncio.sleep(0.01)
            try:
                return await fetch()
            finally:
                running.pop()

        coordinator._fetch = counted
        # A pushed poll and a command's own refresh meet.
        await asyncio.gather(coordinator.async_refresh(), coordinator.async_refresh())
        assert most == 1
        await entry.runtime_data.client.async_close()
        await entry.service.stop()

    asyncio.run(run())
