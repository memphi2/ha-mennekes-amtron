"""Exercise vehicle profile selectors and atomic saves through real HA flows."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mennekes_amtron_rtu.config_schemas import (
    vehicle_detection_entities_schema,
    vehicle_detection_source_schema,
    vehicle_profile_choice_schema,
    vehicle_profile_details_schema,
    vehicle_profile_soc_schema,
    vehicle_source_schema,
)
from custom_components.mennekes_amtron_rtu.vehicle_profiles import profile_list
from tests.fakes import FakeModbusClient, device_bank
from tests.options_flow_helpers import assert_options_http
from tests.test_home_assistant_smoke import _boot
from tests.test_soc_goal_ha_lifecycle import _entry


@pytest.mark.parametrize(
    ("step", "schema"),
    [
        (
            "vehicle_detection_source",
            vehicle_detection_source_schema("profile", profile=True),
        ),
        (
            "vehicle_detection_source",
            vehicle_detection_source_schema("profile", profile=False),
        ),
        ("vehicle_detection_entities", vehicle_detection_entities_schema({})),
        (
            "vehicle_profile_open",
            vehicle_profile_choice_schema(
                [{"value": "1" * 32, "label": "Owner's EV"}], "1" * 32
            ),
        ),
        ("vehicle_profile_add", vehicle_profile_details_schema({})),
        (
            "vehicle_profile_edit",
            vehicle_profile_details_schema(
                {"vehicle_profile_name": "Owner's EV", "vehicle_provider": "none"}
            ),
        ),
        (
            "vehicle_profile_select",
            vehicle_profile_choice_schema(
                [{"value": "1" * 32, "label": "Owner's EV"}], "1" * 32
            ),
        ),
        (
            "vehicle_profile_remove",
            vehicle_profile_choice_schema(
                [{"value": "1" * 32, "label": "Owner's EV"}], "1" * 32, remove=True
            ),
        ),
        ("vehicle_source", vehicle_source_schema({"vehicle_soc_use_profile": True})),
        (
            "vehicle_profile_soc",
            vehicle_profile_soc_schema({"vehicle_limit_use_profile": True}),
        ),
    ],
)
def test_profile_forms_serialize_with_user_labels_and_translated_fields(step, schema):
    assert_options_http(
        {"type": FlowResultType.FORM, "step_id": step, "data_schema": schema}
    )


def test_profile_create_select_and_manual_features_save_through_real_flow_manager(
    tmp_path,
):
    async def run():
        transport = FakeModbusClient(device_bank())
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "ha")
            try:
                entry = await _entry(hass)
                assert await hass.config_entries.async_unload(entry.entry_id)
                manager = hass.config_entries.options

                async def submit(form, value):
                    result = await manager.async_configure(
                        form["flow_id"],
                        {"next_step_id": value} if isinstance(value, str) else value,
                    )
                    assert_options_http(result)
                    return result

                async def begin():
                    form = await manager.async_init(entry.entry_id)
                    return await submit(form, "vehicle")

                async def open_profile(key):
                    form = await submit(await begin(), "vehicle_profile_open")
                    if form["type"] is FlowResultType.FORM:
                        form = await submit(form, {"vehicle_profile_id": key})
                    assert form["step_id"] == "vehicle_profile"
                    return form

                form = await begin()
                assert form["menu_options"] == [
                    "vehicle_profile_add", "vehicle_settings", "back",
                ]
                form = await submit(form, "vehicle_profile_add")
                result = await submit(
                    form,
                    {"vehicle_profile_name": "First EV", "vehicle_provider": "none"},
                )
                assert result["type"] is FlowResultType.CREATE_ENTRY
                first = profile_list(entry.options)[0].id
                form = await open_profile(first)
                assert form["menu_options"] == [
                    "vehicle_profile_edit", "vehicle_battery", "departure", "losses",
                    "vehicle_detection_signals", "vehicle",
                ]
                form = await submit(form, "vehicle_battery")
                assert (await submit(form, "vehicle_profile"))["step_id"] == "vehicle_profile"
                form = await submit(await begin(), "vehicle_profile_add")
                await submit(
                    form,
                    {"vehicle_profile_name": "Second EV", "vehicle_provider": "none"},
                )
                second = profile_list(entry.options)[1].id
                assert entry.options["active_vehicle_profile"] == first
                form = await submit(await begin(), "vehicle_profile_select")
                await submit(form, {"vehicle_profile_id": second})
                assert entry.options["active_vehicle_profile"] == second

                form = await submit(await open_profile(second), "vehicle_battery")
                form = await submit(form, "vehicle_soc")
                await submit(
                    form,
                    {
                        "vehicle_soc_entity": "sensor.external_soc",
                        "use_vehicle_limit": False,
                    },
                )
                assert entry.options["vehicle_profiles"][second]["options"] == {
                    "vehicle_soc_entity": "sensor.external_soc"
                }
                assert entry.options["vehicle_profiles"][first]["options"] == {}

                # A car that is not selected is configured without selecting it.
                selected_at = entry.options["vehicle_profile_selected_at"]
                form = await submit(await open_profile(first), "vehicle_battery")
                form = await submit(form, "vehicle_soc")
                await submit(
                    form,
                    {"vehicle_soc_entity": "sensor.first_soc", "use_vehicle_limit": False},
                )
                assert entry.options["vehicle_profiles"][first]["options"] == {
                    "vehicle_soc_entity": "sensor.first_soc"
                }
                assert entry.options["active_vehicle_profile"] == second
                assert entry.options["vehicle_profile_selected_at"] == selected_at

                form = await submit(await open_profile(second), "vehicle_profile_edit")
                await submit(
                    form,
                    {"vehicle_profile_name": "Renamed EV", "vehicle_provider": "none"},
                )
                assert entry.options["vehicle_profiles"][second]["name"] == "Renamed EV"
                assert entry.options["vehicle_profiles"][second]["options"] == {
                    "vehicle_soc_entity": "sensor.external_soc"
                }
                form = await submit(await open_profile(first), "vehicle_detection_signals")
                assert form["step_id"] == "vehicle_detection_source"
                form = await submit(form, {"vehicle_detection_source": "entities"})
                await submit(
                    form,
                    {
                        "vehicle_connected_entity": "binary_sensor.car_connected",
                        "vehicle_home_entity": "device_tracker.car",
                    },
                )
                assert entry.options["vehicle_profiles"][first]["detection"] == {
                    "source": "entities",
                    "connected_entity": "binary_sensor.car_connected",
                    "home_entity": "device_tracker.car",
                }
                assert not entry.options.get("vehicle_auto_selection")
                assert entry.options["active_vehicle_profile"] == second
                # Automatic selection is chosen in the Active vehicle entity.
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, "vehicle_auto_selection": True},
                )
                form = await submit(await begin(), "vehicle_profile_select")
                await submit(form, {"vehicle_profile_id": second})
                assert entry.options["vehicle_auto_selection"] is False
                form = await submit(await begin(), "vehicle_profile_select")
                await submit(form, {"vehicle_profile_id": first})
                assert entry.options["vehicle_auto_selection"] is False
                await submit(await begin(), "vehicle_profile_clear")
                assert entry.options["active_vehicle_profile"] is None
                assert len(profile_list(entry.options)) == 2
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
