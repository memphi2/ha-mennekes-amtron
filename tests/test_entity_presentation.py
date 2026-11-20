"""Presentation must distinguish controls from detail data without losing states."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory

from custom_components.mennekes_amtron_rtu.device.command_events import (
    ACTIONS,
    OUTCOMES,
    REASONS,
    SOURCES,
)
from custom_components.mennekes_amtron_rtu.device.wallbox_events import (
    ATTRIBUTES as WALLBOX_ATTRIBUTES,
)
from custom_components.mennekes_amtron_rtu.device.wallbox_events import (
    EVENT_TYPES as WALLBOX_EVENT_TYPES,
)
from custom_components.mennekes_amtron_rtu.event import EVENT_TYPES as ACTIVITY_TYPES
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
        assert sensors["operation_waiting"].entity_category is None
        assert sensors["cable_lock_status"].entity_category is EntityCategory.DIAGNOSTIC
        assert sensors["wallbox_settings"].entity_category is EntityCategory.DIAGNOSTIC
        assert selects["requested_phases"].entity_category is None
        assert selects["requested_phases"].extra_state_attributes == {"switched": "all_available"}
        # Naming/category changes must keep the established registry identity.
        assert sensors["wallbox_settings"].unique_id.endswith("_wallbox_settings")
        assert selects["requested_phases"].unique_id.endswith("_requested_phases")

    asyncio.run(run())


def test_only_diagnostics_start_disabled_and_measurements_keep_their_statistics() -> None:
    async def run() -> None:
        hass, entry, _transport = await build_runtime()
        sensors = {}
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        disabled = {key for key, entity in sensors.items() if not entity.entity_registry_enabled_default}
        assert disabled == {
            "authorization_status", "cable_lock_status", "detected_ev_phases",
            "connection_quality", "wallbox_settings",
        }
        assert all(
            sensors[key].entity_category is EntityCategory.DIAGNOSTIC for key in disabled
        )
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


def _activity_attributes(path: Path) -> dict:
    payload = json.loads(path.read_text())
    entity = payload["entity"]["event"]["activity"]
    assert entity["name"], path.name
    return entity["state_attributes"]


def _assert_states(attributes: dict, attribute: str, values: tuple[str, ...], name: str) -> None:
    assert attributes[attribute]["name"], (name, attribute)
    assert set(attributes[attribute]["state"]) == set(values), (name, attribute)
    assert all(attributes[attribute]["state"].values()), (name, attribute)


def test_wallbox_changes_are_fully_translated_in_every_shipped_language() -> None:
    for path in [
        INTEGRATION / "strings.json",
        *sorted((INTEGRATION / "translations").glob("*.json")),
    ]:
        attributes = _activity_attributes(path)
        assert set(WALLBOX_EVENT_TYPES) < set(ACTIVITY_TYPES)
        _assert_states(attributes, "event_type", ACTIVITY_TYPES, path.name)
        assert set(WALLBOX_ATTRIBUTES) <= set(attributes), path.name
        assert all(attributes[key]["name"] for key in attributes), path.name


def test_command_results_are_fully_translated_in_every_shipped_language() -> None:
    for path in [
        INTEGRATION / "strings.json",
        *sorted((INTEGRATION / "translations").glob("*.json")),
    ]:
        attributes = _activity_attributes(path)
        for attribute, values in (
            ("result", OUTCOMES),
            ("action", ACTIONS),
            ("reason", REASONS),
            ("trigger", SOURCES),
        ):
            _assert_states(attributes, attribute, values, path.name)
        assert attributes["occurred_at"]["name"], path.name
        assert attributes["value"]["state"]["0"], path.name


def test_every_attribute_a_sensor_shows_has_a_name_in_every_language() -> None:
    async def run() -> dict[str, set[str]]:
        hass, entry, _transport = await build_runtime()
        sensors = {}
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        sensors["power_total"].coordinator.data.values["power_total"] = 1000.0
        return {key: set(entity.extra_state_attributes or {}) for key, entity in sensors.items()}

    shown = asyncio.run(run())
    assert all(shown[key] for key in ("power_total", "wallbox_settings", "session_energy"))
    for path in [INTEGRATION / "strings.json", *sorted((INTEGRATION / "translations").glob("*.json"))]:
        translated = json.loads(path.read_text())["entity"]["sensor"]
        for key, attributes in shown.items():
            names = translated[key].get("state_attributes", {})
            assert attributes <= set(names), (path.name, key, attributes - set(names))
            assert all(names[attribute]["name"] for attribute in attributes), (path.name, key)
        # Attributes the fixture leaves empty, and the other platforms.
        entities = json.loads(path.read_text())["entity"]
        for platform, key, attributes in (
            ("sensor", "operation_waiting", {"planned_start", "estimated_finish", "estimated_level_at_departure"}),
            ("sensor", "charging_status", {"evse_state", "cp_state", "downgraded_to"}),
            ("binary_sensor", "error", {"code", "category", "heartbeat_lost", "fallback", "fallback_current"}),
            ("select", "requested_phases", {"switched"}),
        ):
            names = entities[platform][key]["state_attributes"]
            assert attributes <= set(names), (path.name, key)
            assert all(names[attribute]["name"] for attribute in attributes), (path.name, key)
        # Counters that only the diagnostics download carries have no entity text.
        assert set(translated["connection_quality"]["state_attributes"]) == {"heartbeat"}
