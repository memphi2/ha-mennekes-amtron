"""Run the shipped PV blueprints as real Home Assistant automations."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict

from custom_components.mennekes_amtron_rtu.blueprint_installer import SOURCE_DIR
from tests.test_home_assistant_smoke import _boot


def _expand(name: str, inputs: dict) -> dict:
    path = SOURCE_DIR / name
    blueprint = Blueprint(
        load_yaml_dict(str(path)),
        expected_domain="automation",
        path=str(path),
        schema=BLUEPRINT_SCHEMA,
    )
    configured = BlueprintInputs(
        blueprint,
        {
            "alias": "PV test",
            "id": "pv_test",
            "use_blueprint": {"path": str(path), "input": inputs},
        },
    )
    configured.validate()
    return configured.async_substitute()


@pytest.mark.parametrize(
    ("pause", "power", "unit", "phases", "expected"),
    [
        ("off", "6900", "W", "all_available", 10),
        ("off", "6.9", "kW", "all_available", 10),
        ("off", "2300", "W", "single_phase", 10),
        ("off", "9000", "W", "single_phase", 16),
        ("off", "-200", "W", "all_available", 6),
        ("on", "6900", "W", "all_available", None),
        ("unavailable", "6900", "W", "all_available", None),
        (None, "6900", "W", "all_available", None),
        ("off", "unavailable", "W", "all_available", None),
        ("off", "nan", "W", "all_available", None),
        ("off", "6900", "VA", "all_available", None),
        ("off", "6900", "W", "unknown", None),
    ],
)
def test_current_blueprint_respects_pause_and_power_units(
    tmp_path: Path,
    pause: str | None,
    power: str,
    unit: str,
    phases: str,
    expected: float | None,
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        calls = []
        try:

            async def record(call):
                calls.append(call.data)

            hass.services.async_register("number", "set_value", record)
            hass.states.async_set("sensor.surplus", "0", {"unit_of_measurement": unit})
            hass.states.async_set("sensor.phases", phases)
            hass.states.async_set("binary_sensor.vehicle", "on")
            hass.states.async_set("number.current", "6", {"max": 16})
            hass.states.async_set("switch.paused", pause or "off")
            inputs = {
                "surplus_power": "sensor.surplus",
                "switched_phases": "sensor.phases",
                "vehicle_connected": "binary_sensor.vehicle",
                "charging_current": "number.current",
            }
            if pause is not None:
                inputs["charging_paused"] = "switch.paused"
            config = _expand("solar_surplus_charging.yaml", inputs)
            assert await async_setup_component(
                hass, "automation", {"automation": [config]}
            )
            await hass.async_block_till_done()
            assert hass.states.get("automation.pv_test").state == "on"
            hass.states.async_set(
                "sensor.surplus", power, {"unit_of_measurement": unit}
            )
            await hass.async_block_till_done()
            assert [call["value"] for call in calls] == (
                [] if expected is None else [expected]
            )
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))


@pytest.mark.parametrize(
    ("power", "unit", "paused", "expected"),
    [
        ("900", "W", "off", "turn_on"),
        ("0.9", "kW", "off", "turn_on"),
        ("2400", "W", "on", "turn_off"),
        ("2.4", "kW", "on", "turn_off"),
        ("900", "W", "on", None),
        ("2400", "W", "off", None),
        ("unavailable", "W", "off", None),
        ("nan", "W", "on", None),
        ("2400", "VA", "on", None),
        ("900", "W", "unavailable", None),
    ],
)
def test_pause_blueprint_converts_power_and_checks_switch(
    tmp_path: Path, power: str, unit: str, paused: str, expected: str | None
) -> None:
    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        calls = []
        try:

            async def record(call):
                calls.append(call.service)

            for service in ("turn_on", "turn_off"):
                hass.services.async_register("switch", service, record)
            hass.states.async_set(
                "sensor.surplus",
                "1.5" if unit == "kW" else "1500",
                {"unit_of_measurement": unit},
            )
            hass.states.async_set("switch.paused", paused)
            config = _expand(
                "pause_and_resume_on_surplus.yaml",
                {
                    "surplus_power": "sensor.surplus",
                    "charging_paused": "switch.paused",
                    # Exercise threshold behavior without wall-clock delays;
                    # control-layer hysteresis is covered in test_control.py.
                    "hold_time": {"seconds": 0},
                },
            )
            assert await async_setup_component(
                hass, "automation", {"automation": [config]}
            )
            await hass.async_block_till_done()
            assert hass.states.get("automation.pv_test").state == "on"
            hass.states.async_set(
                "sensor.surplus", power, {"unit_of_measurement": unit}
            )
            await hass.async_block_till_done()
            assert calls == ([] if expected is None else [expected])
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))


@pytest.mark.parametrize(
    (
        "event",
        "power",
        "unit",
        "pause_input",
        "blueprint_cap",
        "entity_cap",
        "expected",
    ),
    [
        ("resume", "2300", "W", True, 16, 16, 10),
        ("resume", "2.3", "kW", True, 16, 16, 10),
        ("resume", "2300", "W", True, 8, 16, 8),
        ("resume", "2300", "W", True, 16, 8, 8),
        ("resume", "unavailable", "W", True, 16, 16, None),
        ("resume", "2300", "W", False, 16, 16, None),
        ("connect", "2300", "W", True, 16, 16, 10),
        ("phase", "2300", "W", True, 16, 16, 10),
    ],
)
def test_current_blueprint_recalculates_with_a_stable_surplus(
    tmp_path: Path,
    event: str,
    power: str,
    unit: str,
    pause_input: bool,
    blueprint_cap: int,
    entity_cap: int,
    expected: float | None,
) -> None:
    """A budget ignored while paused/disconnected must apply on activation."""

    async def run() -> None:
        hass = await _boot(tmp_path / "config")
        calls = []
        try:

            async def record(call):
                calls.append(call.data["value"])

            hass.services.async_register("number", "set_value", record)
            hass.states.async_set("sensor.surplus", "0", {"unit_of_measurement": unit})
            hass.states.async_set(
                "sensor.phases", "all_available" if event == "phase" else "single_phase"
            )
            hass.states.async_set(
                "binary_sensor.vehicle", "off" if event == "connect" else "on"
            )
            hass.states.async_set("number.current", "16", {"max": entity_cap})
            hass.states.async_set("switch.paused", "on" if event == "resume" else "off")
            inputs = {
                "surplus_power": "sensor.surplus",
                "switched_phases": "sensor.phases",
                "vehicle_connected": "binary_sensor.vehicle",
                "charging_current": "number.current",
                "maximum_current": blueprint_cap,
            }
            if pause_input:
                inputs["charging_paused"] = "switch.paused"
            config = _expand("solar_surplus_charging.yaml", inputs)
            assert await async_setup_component(
                hass, "automation", {"automation": [config]}
            )
            await hass.async_block_till_done()
            assert hass.states.get("automation.pv_test").state == "on"
            hass.states.async_set(
                "sensor.surplus", power, {"unit_of_measurement": unit}
            )
            await hass.async_block_till_done()
            assert calls == ([6] if event == "phase" else [])
            calls.clear()
            entity, value = {
                "resume": ("switch.paused", "off"),
                "connect": ("binary_sensor.vehicle", "on"),
                "phase": ("sensor.phases", "single_phase"),
            }[event]
            # No further surplus update: only the wallbox state changes.
            hass.states.async_set(entity, value)
            await hass.async_block_till_done()
            assert calls == ([] if expected is None else [expected])
        finally:
            await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
