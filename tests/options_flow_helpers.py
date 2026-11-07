"""Drive actual HA menu choices and form submissions for options scenarios."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView

STRINGS = json.loads((Path(__file__).parents[1] / "custom_components/mennekes_amtron_rtu/strings.json").read_text())


def assert_options_http(result):
    """Every emitted form must serialize and display human-readable labels."""
    if result["type"] not in (FlowResultType.FORM, FlowResultType.MENU):
        return
    decoded = json.loads(json.dumps(FlowManagerResourceView(None)._prepare_result_json(result)))
    labels = STRINGS["options"]["step"][decoded["step_id"]]
    if result["type"] is FlowResultType.FORM:
        for field in decoded.get("data_schema", []):
            assert labels.get("data", {}).get(field["name"]), (decoded["step_id"], field["name"])
    for choice in decoded.get("menu_options", []):
        assert labels["menu_options"].get(choice), (decoded["step_id"], choice)


async def async_options(hass, entry, *steps):
    """Use strings for menu choices and dictionaries for submitted fields."""
    manager = hass.config_entries.options
    result = await manager.async_init(entry.entry_id)
    assert_options_http(result)
    for step in steps:
        result = await manager.async_configure(
            result["flow_id"], {"next_step_id": step} if isinstance(step, str) else step,
        )
        assert_options_http(result)
    await hass.async_block_till_done()
    return result
