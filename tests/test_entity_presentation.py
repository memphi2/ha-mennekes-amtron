"""Presentation must distinguish controls from detail data without losing states."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTime

from custom_components.mennekes_amtron_rtu.device.command_events import (
    ACTIONS,
    EVENT_TYPES,
    REASONS,
)
from custom_components.mennekes_amtron_rtu.select import (
    async_setup_entry as setup_selects,
)
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from tests.ha_fakes import build_runtime, collect

INTEGRATION = (
    Path(__file__).resolve().parents[1] / "custom_components" / "mennekes_amtron_rtu"
)


def test_main_feedback_and_controls_remain_primary() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        sensors = {}
        selects = {}
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        await setup_selects(hass, entry, lambda items: selects.update(collect(items)))

        assert sensors["evse_state"].entity_category is None
        assert sensors["switched_phases"].entity_category is None
        assert sensors["pending_change"].entity_category is None
        assert sensors["cp_state"].entity_category is EntityCategory.DIAGNOSTIC
        assert sensors["error_code"].entity_category is EntityCategory.DIAGNOSTIC
        assert selects["requested_phases"].entity_category is None
        assert selects["solar_charging_mode"].entity_category is EntityCategory.CONFIG
        # Naming/category changes must keep the established registry identity.
        assert sensors["switched_phases"].unique_id.endswith("_switched_phases")
        assert selects["solar_charging_mode"].unique_id.endswith("_solar_charging_mode")

    asyncio.run(run())


def test_optional_detail_entities_start_disabled() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        sensors = {}
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        disabled = {
            key
            for key, entity in sensors.items()
            if not entity.entity_registry_enabled_default
        }
        assert disabled == {
            "modbus_layout_version",
            "voltage_l1",
            "voltage_l2",
            "voltage_l3",
            "power_l1",
            "power_l2",
            "power_l3",
        }
        # Detail measurements remain usable after users enable them.
        assert sensors["voltage_l1"].native_value is not None
        assert sensors["power_l1"].state_class is SensorStateClass.MEASUREMENT
        assert sensors["energy_total"].state_class is SensorStateClass.TOTAL_INCREASING

    asyncio.run(run())


def test_session_duration_keeps_live_value_without_long_term_statistics() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        sensors = {}
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        duration = sensors["session_duration"]
        assert duration.native_value == 600
        assert duration.device_class is SensorDeviceClass.DURATION
        assert duration.native_unit_of_measurement == UnitOfTime.SECONDS
        assert duration.entity_registry_enabled_default
        assert duration.state_class is None

    asyncio.run(run())


def test_icon_states_match_translated_states_without_claiming_three_phases() -> None:
    icons = json.loads((INTEGRATION / "icons.json").read_text())["entity"]
    strings = json.loads((INTEGRATION / "strings.json").read_text())["entity"]
    for platform, entities in icons.items():
        for key, definition in entities.items():
            states = definition.get("state", {})
            supported = (
                {"on", "off"}
                if platform in {"binary_sensor", "switch"}
                else strings[platform][key].get("state", {})
            )
            assert set(states) <= set(supported), (platform, key)
    # All available phases also covers a single-phase supply.
    for platform, key in (
        ("select", "requested_phases"),
        ("sensor", "switched_phases"),
    ):
        definition = icons[platform][key]
        all_phases = definition["state"].get("all_available", definition["default"])
        assert "numeric-3" not in all_phases


def test_command_results_are_fully_translated_in_every_shipped_language() -> None:
    for path in [
        INTEGRATION / "strings.json",
        *sorted((INTEGRATION / "translations").glob("*.json")),
    ]:
        payload = json.loads(path.read_text())
        attributes = payload["entity"]["event"]["control_result"]["state_attributes"]
        for attribute, values in (
            ("event_type", EVENT_TYPES),
            ("action", ACTIONS),
            ("reason", REASONS),
        ):
            assert attributes[attribute]["name"], (path.name, attribute)
            assert set(attributes[attribute]["state"]) == set(values), (
                path.name,
                attribute,
            )
            assert all(attributes[attribute]["state"].values()), (path.name, attribute)
