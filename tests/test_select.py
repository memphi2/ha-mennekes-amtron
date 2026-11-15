from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.select import async_setup_entry
from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_PROFILES,
)
from tests.fakes import device_bank
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect

FIRST = "a" * 32
SECOND = "b" * 32
THIRD = "c" * 32


async def _selects(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry, transport


def test_the_phase_select_exists_only_on_capable_hardware() -> None:
    async def run() -> None:
        selects, _entry, transport = await _selects()
        assert set(selects) == {"requested_phases"}
        assert selects["requested_phases"].current_option == "all_available"
        transport.bank[R.CP_STATE.address] = int(R.CpState.A1)
        await selects["requested_phases"].async_select_option("single_phase")
        assert dict(transport.writes)[R.REQUESTED_PHASES.address] == [1]

        gated, _entry2, _transport2 = await _selects(bank=device_bank(phase_options=1))
        assert "requested_phases" not in gated

    asyncio.run(run())


def test_a_read_only_entry_offers_no_phase_select() -> None:
    selects, _entry, _transport = asyncio.run(_selects(read_only=True))
    assert selects == {}


def test_unknown_register_values_yield_no_option() -> None:
    async def run() -> None:
        selects, entry, _transport = await _selects()
        values = entry.runtime_data.coordinator.data.values
        values["requested_phases"] = 9
        values["switched_phases"] = 9
        assert selects["requested_phases"].current_option is None
        assert selects["requested_phases"].extra_state_attributes is None

    asyncio.run(run())


def _profile(name: str, *, detection: bool = False) -> dict[str, object]:
    profile: dict[str, object] = {"name": name, "provider": "none", "device_id": "", "options": {}}
    if detection:
        profile["detection"] = {
            "source": "entities",
            "connected_entity": "binary_sensor.car_plugged",
            "home_entity": "device_tracker.car",
        }
    return profile


def test_vehicle_names_stay_distinct_and_automatic_selection_is_offered_with_a_detector() -> None:
    async def run() -> None:
        entry = FakeConfigEntry(options={
            "control_mode": "master",
            CONF_VEHICLE_PROFILES: {
                FIRST: _profile("none", detection=True),
                SECOND: _profile("Car"),
            },
            CONF_ACTIVE_VEHICLE_PROFILE: SECOND,
        })
        selects, entry, transport = await _selects(entry=entry)
        vehicle = selects["active_vehicle"]
        vehicle.hass = entry.runtime_data.coordinator.hass
        # A car named like a fixed choice still gets its own label.
        assert vehicle.options == ["none", "automatic", "none (2)", "Car"]
        assert vehicle.current_option == "Car"
        assert vehicle.available
        assert vehicle.extra_state_attributes == {"active_vehicle": "Car"}

        await vehicle.async_select_option("automatic")
        assert entry.options[CONF_VEHICLE_AUTO_SELECTION] is True
        assert vehicle.current_option == "automatic"

        await vehicle.async_select_option("none (2)")
        assert entry.options[CONF_ACTIVE_VEHICLE_PROFILE] == FIRST
        assert entry.options[CONF_VEHICLE_AUTO_SELECTION] is False
        assert vehicle.current_option == "none (2)"
        assert vehicle.extra_state_attributes == {"active_vehicle": "none"}

        await vehicle.async_select_option("none")
        assert entry.options[CONF_ACTIVE_VEHICLE_PROFILE] is None
        assert vehicle.current_option == "none"
        assert vehicle.extra_state_attributes == {"active_vehicle": None}
        with pytest.raises(ServiceValidationError):
            await vehicle.async_select_option("Missing car")
        # Choosing a vehicle is a setting of the entry, never a wallbox write.
        assert transport.writes == []

    asyncio.run(run())


def test_a_label_already_taken_moves_on_to_the_next_number() -> None:
    async def run() -> None:
        entry = FakeConfigEntry(options={
            CONF_VEHICLE_PROFILES: {FIRST: _profile("none (2)"), SECOND: _profile("Van"), THIRD: _profile("none")},
        })
        selects, _entry, _transport = await _selects(entry=entry, read_only=True)
        assert set(selects) == {"active_vehicle"}
        assert selects["active_vehicle"].options == ["none", "none (2)", "Van", "none (3)"]

    asyncio.run(run())
