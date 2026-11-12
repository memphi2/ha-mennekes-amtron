"""Presentation must distinguish controls from detail data without losing states."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory

from custom_components.mennekes_amtron_rtu.device.command_events import (
    ACTIONS,
    EVENT_TYPES,
    OUTCOMES,
    REASONS,
)
from custom_components.mennekes_amtron_rtu.device.wallbox_events import (
    ATTRIBUTES as WALLBOX_ATTRIBUTES,
)
from custom_components.mennekes_amtron_rtu.device.wallbox_events import (
    EVENT_TYPES as WALLBOX_EVENT_TYPES,
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

        assert sensors["charging_status"].entity_category is None
        assert sensors["pending_change"].entity_category is None
        assert sensors["cable_lock_status"].entity_category is EntityCategory.DIAGNOSTIC
        assert sensors["wallbox_settings"].entity_category is EntityCategory.DIAGNOSTIC
        assert selects["requested_phases"].entity_category is None
        assert selects["requested_phases"].extra_state_attributes == {"switched": "all_available"}
        assert selects["solar_charging_mode"].entity_category is EntityCategory.CONFIG
        # Naming/category changes must keep the established registry identity.
        assert sensors["wallbox_settings"].unique_id.endswith("_wallbox_settings")
        assert selects["solar_charging_mode"].unique_id.endswith("_solar_charging_mode")

    asyncio.run(run())


def test_every_sensor_starts_enabled_and_measurements_keep_their_statistics() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        sensors = {}
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        assert all(entity.entity_registry_enabled_default for entity in sensors.values())
        assert sensors["power_total"].state_class is SensorStateClass.MEASUREMENT
        assert sensors["energy_total"].state_class is SensorStateClass.TOTAL_INCREASING
        assert sensors["session_energy"].state_class is SensorStateClass.TOTAL_INCREASING
        # Details ride along as attributes, without statistics of their own.
        sensors["power_total"].coordinator.data.values["power_total"] = 1000.0
        assert sensors["power_total"].extra_state_attributes["voltage_l1"] == 230
        assert sensors["session_energy"].extra_state_attributes["duration_minutes"] == 10
        assert sensors["wallbox_settings"].device_class is None

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
    definition = icons["select"]["requested_phases"]
    all_phases = definition["state"].get("all_available", definition["default"])
    assert "numeric-3" not in all_phases


def test_wallbox_events_are_fully_translated_in_every_shipped_language() -> None:
    for path in [
        INTEGRATION / "strings.json",
        *sorted((INTEGRATION / "translations").glob("*.json")),
    ]:
        payload = json.loads(path.read_text())
        entity = payload["entity"]["event"]["wallbox_event"]
        assert entity["name"], path.name
        attributes = entity["state_attributes"]
        assert set(attributes["event_type"]["state"]) == set(WALLBOX_EVENT_TYPES), path.name
        assert all(attributes["event_type"]["state"].values()), path.name
        assert set(attributes) == {"event_type", *WALLBOX_ATTRIBUTES}, path.name
        assert all(attributes[key]["name"] for key in attributes), path.name


def test_command_results_are_fully_translated_in_every_shipped_language() -> None:
    for path in [
        INTEGRATION / "strings.json",
        *sorted((INTEGRATION / "translations").glob("*.json")),
    ]:
        payload = json.loads(path.read_text())
        attributes = payload["entity"]["event"]["control_result"]["state_attributes"]
        for attribute, values in (
            ("event_type", EVENT_TYPES),
            ("result", OUTCOMES),
            ("action", ACTIONS),
            ("reason", REASONS),
        ):
            assert attributes[attribute]["name"], (path.name, attribute)
            assert set(attributes[attribute]["state"]) == set(values), (
                path.name,
                attribute,
            )
            assert all(attributes[attribute]["state"].values()), (path.name, attribute)
