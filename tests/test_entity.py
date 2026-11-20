from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.helpers.entity import EntityDescription

from custom_components.mennekes_amtron_rtu.binary_sensor import BINARY_SENSORS
from custom_components.mennekes_amtron_rtu.binary_sensor import (
    async_setup_entry as setup_binary_sensors,
)
from custom_components.mennekes_amtron_rtu.button import BUTTONS
from custom_components.mennekes_amtron_rtu.button import (
    async_setup_entry as setup_buttons,
)
from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
)
from custom_components.mennekes_amtron_rtu.const import (
    CONF_CONTROL_MODE,
    CONF_SERIAL_NUMBER,
    CONTROL_MODE_MASTER,
)
from custom_components.mennekes_amtron_rtu.datetime import (
    async_setup_entry as setup_datetime,
)
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from custom_components.mennekes_amtron_rtu.entity import (
    AmtronEntity,
    Projection,
    entry_device_info,
    entry_device_key,
    entry_entity_unique_id,
    wanted,
)
from custom_components.mennekes_amtron_rtu.number import NUMBERS
from custom_components.mennekes_amtron_rtu.number import (
    async_setup_entry as setup_numbers,
)
from custom_components.mennekes_amtron_rtu.select import SELECTS
from custom_components.mennekes_amtron_rtu.select import (
    async_setup_entry as setup_selects,
)
from custom_components.mennekes_amtron_rtu.sensor import SENSORS
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from custom_components.mennekes_amtron_rtu.switch import SWITCHES
from custom_components.mennekes_amtron_rtu.switch import (
    async_setup_entry as setup_switches,
)
from custom_components.mennekes_amtron_rtu.vehicle_features import LOSS
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect


def test_the_serial_number_is_the_device_key() -> None:
    entry = FakeConfigEntry(data={CONF_SERIAL_NUMBER: "ABC123"})
    assert entry_device_key(entry) == "ABC123"
    assert entry_entity_unique_id(entry, "evse_state") == "ABC123_evse_state"


def test_without_a_serial_number_the_entry_id_is_used() -> None:
    entry = FakeConfigEntry(entry_id="xyz", data={})
    assert entry_device_key(entry) == "xyz"
    assert entry_entity_unique_id(entry, "charging") == "xyz_charging"


def test_device_info_carries_the_identity() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        info = entry_device_info(entry)
        assert info["identifiers"] == {(DOMAIN, "ABC123456789")}
        assert info["manufacturer"] == "Mennekes"
        assert info["model"] == "Amtron 4You 310 11 C2"
        assert info["sw_version"] == "2023.21.11024"
        assert info["serial_number"] == "ABC123456789"
        assert info["model_id"] == "1313201205"

    asyncio.run(run())


@pytest.mark.parametrize("article", ["______1313201205", "____1313201205BK"])
def test_padding_is_removed_only_from_display_and_known_articles_are_mapped(article: str) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        serial = "____00TEST_123"
        entry.data[CONF_SERIAL_NUMBER] = serial
        entry.runtime_data.identity.serial_number = serial
        entry.runtime_data.identity.article_number = article
        info = entry_device_info(entry)
        assert info["serial_number"] == "00TEST_123"
        assert info["model"] == "Amtron 4You 310 11 C2"
        assert info["model_id"] == article.lstrip("_")
        assert info["identifiers"] == {(DOMAIN, serial)}
        assert entry_entity_unique_id(entry, "evse_state") == f"{serial}_evse_state"
        assert entry.runtime_data.identity.serial_number == serial

    asyncio.run(run())


@pytest.mark.parametrize("article", ["__UNKNOWN_123", "1313201205OTHER", "____________", None])
def test_unknown_articles_are_not_mistaken_for_a_known_model(article: str | None) -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        entry.runtime_data.identity.article_number = article
        entry.runtime_data.identity.serial_number = "________________"
        info = entry_device_info(entry)
        assert info["model"] == "Amtron"
        assert "serial_number" not in info
        if article and article.lstrip("_"):
            assert info["model_id"] == article.lstrip("_")
        else:
            assert "model_id" not in info

    asyncio.run(run())


def test_device_info_falls_back_to_a_generic_model() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        entry.runtime_data.identity.article_number = None
        entry.runtime_data.identity.firmware_version = None
        entry.runtime_data.identity.serial_number = None
        info = entry_device_info(entry)
        assert info["model"] == "Amtron"
        assert "sw_version" not in info
        assert "serial_number" not in info

    asyncio.run(run())


@dataclass(frozen=True, kw_only=True)
class _Description(Projection, EntityDescription):
    pass


def test_an_entity_is_unavailable_without_its_register() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        coordinator = entry.runtime_data.coordinator
        entity = AmtronEntity(entry, _Description(key="evse_state", register="evse_state"))
        assert entity.unique_id == "ABC123456789_evse_state"
        assert entity.translation_key == "evse_state"
        assert entity.available
        assert entity.runtime is entry.runtime_data
        assert entity.wallbox_data.has("evse_state")
        assert entity.extra_state_attributes is None

        missing = AmtronEntity(entry, _Description(key="nothing", register="temperature"))
        coordinator.data.values["temperature"] = None
        assert not missing.available

        coordinator.last_update_success = False
        assert not entity.available

    asyncio.run(run())


def test_a_description_must_be_a_projection() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        with pytest.raises(TypeError):
            AmtronEntity(entry, EntityDescription(key="evse_state"))

    asyncio.run(run())


def test_a_control_needs_write_access_and_its_readiness() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        ready = [True]
        control = AmtronEntity(entry, _Description(key="control", writes=True, ready=lambda _runtime: ready[0]))
        assert control.available
        ready[0] = False
        assert not control.available
        ready[0] = True
        entry.runtime_data.client.snapshot["control"]["read_only"] = True
        assert not control.available

    asyncio.run(run())


def test_an_offline_entity_follows_only_its_readiness() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        ready = [True]
        entity = AmtronEntity(entry, _Description(
            key="queued", offline=True, register="temperature", writes=True,
            ready=lambda _runtime: ready[0],
        ))
        coordinator = entry.runtime_data.coordinator
        coordinator.last_update_success = False
        coordinator.data.values["temperature"] = None
        entry.runtime_data.client.snapshot["control"]["read_only"] = True
        # Usable from the service's last state while the wallbox is down.
        assert entity.available
        ready[0] = False
        assert not entity.available

    asyncio.run(run())


def test_attributes_without_a_value_are_left_out() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        values = [{"a": 1, "b": None}]
        entity = AmtronEntity(entry, _Description(key="x", attributes=lambda _runtime: values[0]))
        assert entity.extra_state_attributes == {"a": 1}
        values[0] = {"b": None}
        assert entity.extra_state_attributes is None

    asyncio.run(run())


def test_a_vehicle_switch_publishes_only_its_result() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime()
        entity = AmtronEntity(entry, _Description(key="evse_state", register="evse_state"))
        writes: list[int] = []
        entity.async_write_ha_state = lambda: writes.append(1)
        entry.runtime_data.vehicle_switching = True
        entity._handle_coordinator_update()
        assert writes == []
        entry.runtime_data.vehicle_switching = False
        entity._handle_coordinator_update()
        assert writes == [1]

    asyncio.run(run())


def test_only_rows_the_wallbox_and_vehicles_call_for_are_wanted() -> None:
    async def run() -> None:
        _hass, entry, _transport = await build_runtime(layout=R.LAYOUT_V01_02)
        rows = (
            _Description(key="plain"),
            _Description(key="restart", capability=R.SYSTEM_RESTART.key, register="evse_state"),
            _Description(key="temperature", register="temperature"),
            _Description(key="loss", feature=lambda features: LOSS in features),
            _Description(key="never", exists=lambda _runtime: False),
        )
        assert [row.key for row in wanted(entry, rows)] == ["plain", "temperature"]
        entry.options = {**entry.options, CONF_CHARGE_LOSS_SOURCE: "sensor"}
        assert [row.key for row in wanted(entry, rows)] == ["plain", "temperature", "loss"]

    asyncio.run(run())


class _Trip:
    def __init__(self) -> None:
        self.set: list[object] = []

    async def async_set_deadline(self, value) -> None:
        self.set.append(value)


def test_the_target_time_is_the_services_and_a_selected_car_adds_its_source() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        added: dict[str, object] = {}
        await setup_datetime(hass, entry, lambda items: added.update(collect(items)))
        target = added["departure_time"]
        runtime = entry.runtime_data
        deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=6)
        # Without a car's plan, the service keeps the time itself.
        assert target.available == (runtime.control.departure is not None)
        await target.async_set_value(deadline)
        assert target.available
        assert target.native_value == deadline
        await target.async_clear_value()
        assert target.native_value is None

        # A selected car's observer takes the edit, to mark the source it replaces.
        trip = _Trip()
        runtime.departure_observer = trip
        assert target.native_value is None
        await target.async_set_value(deadline)
        await target.async_clear_value()
        assert trip.set == [deadline, None]
        # What is shown is always the service's time, never a copy of the car's.
        runtime.departure_observer = None
        await target.async_set_value(deadline)
        runtime.departure_observer = trip
        assert target.native_value == deadline
        # Without the service's plan there is no time to show or to set.
        runtime.client.departure = None
        assert target.native_value is None
        assert not target.available

    asyncio.run(run())


_PLATFORMS = {
    "sensor": (setup_sensors, SENSORS),
    "binary_sensor": (setup_binary_sensors, BINARY_SENSORS),
    "switch": (setup_switches, SWITCHES),
    "number": (setup_numbers, NUMBERS),
    "select": (setup_selects, SELECTS),
    "button": (setup_buttons, BUTTONS),
}


async def _every_entity():
    entry = FakeConfigEntry(options={
        CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "vehicle_soc_entity": "sensor.car_soc",
        CONF_CHARGE_LOSS_SOURCE: "sensor",
    })
    hass, entry, transport = await build_runtime(entry=entry)
    runtime = entry.runtime_data
    runtime.plug_charge = SimpleNamespace(status="disabled", blocked_reason=None)
    runtime.charging_cost = SimpleNamespace(available=False, current=None, last=SimpleNamespace(result=None))
    runtime.cost_goal = SimpleNamespace(target=0.0, status="disabled")
    added: dict[str, dict[str, object]] = {}
    for platform, (setup, _rows) in _PLATFORMS.items():
        added[platform] = {}
        await setup(hass, entry, lambda items, platform=platform: added[platform].update(collect(items)))
    return entry, transport, added


def test_every_table_row_becomes_an_entity_of_its_wallbox() -> None:
    async def run() -> None:
        entry, transport, added = await _every_entity()
        transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        await entry.runtime_data.coordinator.async_refresh()
        for platform, (_setup, rows) in _PLATFORMS.items():
            assert set(added[platform]) >= {row.key for row in rows}, platform
            for key, entity in added[platform].items():
                assert entity.unique_id == f"ABC123456789_{key}"
                assert entity.device_info["identifiers"] == {(DOMAIN, "ABC123456789")}
        # What the simulated wallbox holds is what the entities show.
        sensors, binary = added["sensor"], added["binary_sensor"]
        assert sensors["signaled_current"].native_value == 0.0
        assert sensors["temperature"].native_value is not None
        assert sensors["session_energy"].native_value is not None
        assert binary["vehicle_connected"].is_on is True
        assert binary["error"].is_on is False
        assert added["switch"]["charging_release"].is_on is True
        assert added["switch"]["lock_evse"].is_on is False
        assert added["number"]["charging_current_limit"].native_value == 16.0
        assert added["select"]["requested_phases"].current_option == "all_available"
        assert entry.runtime_data.coordinator.data.values["charging_current_ems"] == 16.0

    asyncio.run(run())


@pytest.mark.parametrize(("platform", "key", "act", "register", "words"), [
    ("switch", "charging_paused", lambda entity: entity.async_turn_on(), R.CHARGING_CURRENT_EMS, None),
    ("switch", "charging_release", lambda entity: entity.async_turn_off(), R.CHARGING_RELEASE, [0]),
    ("switch", "lock_evse", lambda entity: entity.async_turn_on(), R.LOCK_EVSE, [1]),
    ("number", "charging_current_limit", lambda entity: entity.async_set_native_value(10.0), R.CHARGING_CURRENT_EMS, None),
    ("select", "requested_phases", lambda entity: entity.async_select_option("single_phase"), R.REQUESTED_PHASES, [1]),
    ("button", "restart", lambda entity: entity.async_press(), R.SYSTEM_RESTART, [0xBB]),
])
def test_every_control_writes_its_register(platform, key, act, register, words) -> None:
    async def run() -> None:
        entry, transport, added = await _every_entity()
        # Idle and unplugged: phases may switch and the wallbox may restart.
        transport.bank[R.CP_STATE.address] = int(R.CpState.A1)
        await entry.runtime_data.coordinator.async_refresh()
        await act(added[platform][key])
        flush = entry.service.control._flush_task
        if flush is not None:
            await asyncio.wait_for(flush, 1)
        written = [value for address, value in transport.writes if address == register.address]
        assert written, (key, transport.writes)
        if words is not None:
            assert written[-1] == words

    asyncio.run(run())
