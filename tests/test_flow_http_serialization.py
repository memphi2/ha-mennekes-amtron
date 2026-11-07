"""Exercise the HTTP serialization layer that a flow-only fake cannot cover."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import probatio
import pytest
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView

from custom_components.mennekes_amtron_rtu.config_schemas import (
    advanced_schema,
    cost_network_schema,
    cost_windows_schema,
    departure_calendar_schema,
    departure_capacity_schema,
    departure_configure_schema,
    departure_entity_schema,
    loss_sensor_schema,
    network_schema,
    options_schema,
    user_schema,
    vehicle_device_schema,
    vehicle_limit_schema,
    vehicle_provider_schema,
    vehicle_soc_schema,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.options_flow_helpers import assert_options_http
from tests.test_charging_cost_options import prices, windows
from tests.test_home_assistant_smoke import _boot
from tests.test_soc_goal_ha_lifecycle import _entry


def _schema(name):
    if name == "serial":
        return user_schema([])
    if name == "network":
        return network_schema()
    if name in ("direct", "service"):
        return options_schema({}, max_current=16, service_mode=name == "service")
    if name == "advanced":
        return advanced_schema({}, phase_switch=True)
    return cost_network_schema({}, module=name)


@pytest.mark.parametrize("name", [
    "serial", "network", "direct", "service", "advanced", "module_2", "module_3",
])
def test_http_view_serializes_every_integer_bearing_form(name):
    view = FlowManagerResourceView(None)
    response = view._prepare_result_json({
        "type": FlowResultType.FORM, "step_id": name, "data_schema": _schema(name),
    })
    # The HTTP layer must be able to encode the complete response, including
    # selectors, suggested values and defaults, before presenting the form.
    decoded = json.loads(json.dumps(response))
    assert isinstance(decoded["data_schema"], list)
    numbers = [field["selector"]["number"] for field in decoded["data_schema"]
               if "number" in field.get("selector", {})]
    assert numbers
    assert all(number["mode"] == "box" for number in numbers)


INTEGER_FIELDS = [
    ("serial", "device_id", 50),
    ("network", "device_id", 50),
    ("network", "tcp_port", 502),
    ("advanced", "state_change_interval_seconds", 300),
    ("advanced", "phase_switch_grace_seconds", 5),
    ("module_3", "tariff_year", 2026),
]


@pytest.mark.parametrize(("name", "key", "value"), INTEGER_FIELDS)
def test_serializable_selectors_keep_whole_ui_numbers_and_reject_fractions(name, key, value):
    schema = _schema(name)
    field = next(selector for marker, selector in schema.schema.items() if marker.schema == key)
    for submitted in (value, float(value)):
        result = field(submitted)
        assert result == value
        assert type(result) is int
    for invalid in (value + 0.5, True, str(value), float("nan"), float("inf"), 10**1000):
        with pytest.raises(probatio.Invalid):
            field(invalid)


@pytest.mark.parametrize(("step", "schema"), [
    ("vehicle_soc", vehicle_soc_schema({})),
    ("vehicle_limit", vehicle_limit_schema({})),
    ("departure_configure", departure_configure_schema({}, vehicle_timer=False)),
    ("departure_capacity_fixed", departure_capacity_schema({}, entity=False)),
    ("departure_capacity_entity", departure_capacity_schema({}, entity=True)),
    ("departure_calendar", departure_calendar_schema({})),
    ("departure_entity", departure_entity_schema({})),
    ("departure_vehicle", vehicle_provider_schema("timed_vehicle_provider", "none", ["bavariandata"])),
    ("departure_vehicle_details", vehicle_device_schema({}, key="timed_vehicle_device", integration="bavariandata", timer_options=True)),
    ("loss_vehicle", vehicle_provider_schema("charge_loss_vehicle_provider", "none", ["bavariandata"])),
    ("loss_vehicle_details", vehicle_device_schema({}, key="charge_loss_vehicle_device", integration="bavariandata")),
    ("loss_sensor", loss_sensor_schema({})),
    ("cost_module3_windows", cost_windows_schema({})),
])
def test_optional_forms_serialize_with_translated_field_labels(step, schema):
    assert_options_http({"type": FlowResultType.FORM, "step_id": step, "data_schema": schema})


@pytest.mark.parametrize("service", [False, True])
def test_real_http_options_can_save_module_three_then_back_to_two_and_standard(tmp_path, service):
    async def run():
        transport = FakeModbusClient(device_bank())
        with (
            patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport),
            patch("custom_components.mennekes_amtron_rtu._options_estimates.async_lookup_home_tariff", new=AsyncMock(return_value=None)),
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                hass.config.country = "DE"
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                manager = hass.config_entries.options
                view = FlowManagerResourceView(manager)

                def serialized(result):
                    assert_options_http(result)
                    decoded = json.loads(json.dumps(view._prepare_result_json(result)))
                    assert decoded["type"] in ("form", "menu", "create_entry")
                    return result

                async def submit(form, value):
                    data = {"next_step_id": value} if isinstance(value, str) else value
                    return serialized(await manager.async_configure(form["flow_id"], data))

                # Exercise actual menu routes, not only selector objects. These
                # delay fields previously broke HTTP JSON before rendering.
                form = serialized(await manager.async_init(entry.entry_id))
                form = await submit(form, "wallbox")
                form = await submit(form, "wallbox_advanced")
                assert form["step_id"] == "wallbox_advanced"
                manager.async_abort(form["flow_id"])

                if service:
                    hass.config_entries.async_update_entry(entry, data={
                        "server_url": "http://unused.test:8765", "server_token": "unused",
                    })

                form = serialized(await manager.async_init(entry.entry_id))
                form = await submit(form, "costs")
                form = await submit(form, "cost_fixed")
                form = await submit(form, {"charging_price": 0.3, "charging_network_module": "module_3"})
                assert form["step_id"] == "cost_module3_source"
                form = await submit(form, "cost_module3_manual")
                assert form["step_id"] == "cost_module3_prices"
                for invalid in (2026.5, True, "2026", float("nan"), float("inf"), 10**1000):
                    with pytest.raises(InvalidData):
                        await manager.async_configure(form["flow_id"], {**prices(), "tariff_year": invalid})
                form = await submit(form, prices())
                assert form["step_id"] == "cost_module3_windows"
                form = await submit(form, windows())
                assert form["step_id"] == "review"
                assert "charging_network_tariff" not in entry.options
                form = await submit(form, "save")
                assert form["type"] is FlowResultType.CREATE_ENTRY
                assert entry.options["charging_network_tariff"]["module"] == "module_3"

                # Reproduce the user's exact sequence in the manager/HTTP layer:
                # enter Module 3, go back, then save Module 2 or standard pricing.
                for replacement in ("module_2", "none"):
                    before = dict(entry.options)
                    form = serialized(await manager.async_init(entry.entry_id))
                    form = await submit(form, "costs")
                    form = await submit(form, "cost_fixed")
                    form = await submit(form, {"charging_price": 0.9, "charging_network_module": "module_3"})
                    form = await submit(form, "back")
                    assert form["step_id"] == "init"
                    assert entry.options == before
                    form = await submit(form, "costs")
                    form = await submit(form, "cost_fixed")
                    form = await submit(form, {"charging_price": 0.3, "charging_network_module": replacement})
                    if replacement == "module_2":
                        assert form["step_id"] == "cost_module2"
                        form = await submit(form, {"regular_network": 0.1, "price_includes_network": True})
                    assert form["step_id"] == "review"
                    assert entry.options == before
                    form = await submit(form, "save")
                    assert form["type"] is FlowResultType.CREATE_ENTRY
                    assert entry.options["charging_network_tariff"]["module"] == replacement
                    assert entry.options["charging_price"] == 0.3
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
