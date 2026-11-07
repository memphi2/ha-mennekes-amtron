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
    cost_network_schema,
    network_schema,
    options_schema,
    user_schema,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_charging_cost_options import NETWORK
from tests.test_home_assistant_smoke import _boot
from tests.test_soc_goal_ha_lifecycle import _entry


def _schema(name):
    if name == "serial":
        return user_schema([])
    if name == "network":
        return network_schema()
    if name in ("direct", "service"):
        return options_schema({}, max_current=16, service_mode=name == "service")
    return cost_network_schema({}, module=name)


@pytest.mark.parametrize("name", [
    "serial", "network", "direct", "service", "module_2", "module_3",
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
    ("direct", "state_change_interval_seconds", 300),
    ("direct", "phase_switch_grace_seconds", 5),
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


def test_real_options_http_flow_can_save_module_three_then_module_two_and_standard(tmp_path):
    async def run():
        transport = FakeModbusClient(device_bank())
        with (
            patch("custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient", return_value=transport),
            patch("custom_components.mennekes_amtron_rtu.options_flow.async_lookup_home_tariff", new=AsyncMock(return_value=None)),
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                hass.config.country = "DE"
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                manager = hass.config_entries.options
                view = FlowManagerResourceView(manager)

                # Direct options used to fail on their new delay fields before
                # the browser could even show the first settings page.
                direct = await manager.async_init(entry.entry_id)
                json.dumps(view._prepare_result_json(direct))
                manager.async_abort(direct["flow_id"])

                # Use service options to reach the exact reported failure:
                # selecting Module 3 advanced the manager but the HTTP view
                # failed, leaving the browser displaying the old source form.
                hass.config_entries.async_update_entry(entry, data={
                    "server_url": "http://unused.test:8765", "server_token": "unused",
                })
                for module in ("module_3", "module_2", "none"):
                    form = await manager.async_init(entry.entry_id)
                    json.dumps(view._prepare_result_json(form))
                    form = await manager.async_configure(
                        form["flow_id"], form["data_schema"]({"charging_cost_source": "fixed"}),
                    )
                    assert form["step_id"] == "cost_fixed"
                    json.dumps(view._prepare_result_json(form))
                    form = await manager.async_configure(form["flow_id"], {
                        "charging_price": 0.3, "charging_network_module": module,
                    })
                    json.dumps(view._prepare_result_json(form))
                    if module != "none":
                        assert form["step_id"] == "cost_network"
                        details = dict(NETWORK) if module == "module_3" else {
                            "regular_network": 0.1, "price_includes_network": True,
                        }
                        if module == "module_3":
                            with pytest.raises(InvalidData):
                                await manager.async_configure(form["flow_id"], {
                                    **details, "tariff_year": 2026.5,
                                })
                        form = await manager.async_configure(form["flow_id"], details)
                    assert form["type"] is FlowResultType.CREATE_ENTRY
                    assert entry.options["charging_network_tariff"]["module"] == module
                    json.dumps(view._prepare_result_json(form))
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
