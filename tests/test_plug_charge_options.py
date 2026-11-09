"""Virtual Plug & Charge has one switch; profiles only supply detection."""

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    CONF_VEHICLE_PLUG_CHARGE,
    CONF_VIRTUAL_PLUG_CHARGE,
    profiles,
)
from tests.ha_fakes import FakeConfigEntry
from tests.test_vehicle_detection_options import detected_options, select_detector
from tests.test_vehicle_profile_options import Flow, open_profile
from tests.test_vehicle_profiles import A, B


def enabled_options():
    raw = detected_options()
    raw[CONF_VIRTUAL_PLUG_CHARGE] = True
    return raw


def test_profile_offers_detection_but_no_second_permission():
    async def run():
        flow = Flow(FakeConfigEntry(options=enabled_options()))
        menu = await open_profile(flow)
        assert "vehicle_detection_signals" in menu["menu_options"]
        assert "vehicle_plug_charge" not in menu["menu_options"]
        assert not hasattr(flow, "async_step_vehicle_plug_charge")

    asyncio.run(run())


def test_a_stored_former_permission_stays_valid_and_is_kept():
    raw = enabled_options()
    raw["vehicle_profiles"][A]["options"][CONF_VEHICLE_PLUG_CHARGE] = True
    assert profiles(raw)[A].options == {CONF_VEHICLE_PLUG_CHARGE: True}


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_malformed_stored_permission_still_invalidates_the_catalog(value):
    raw = enabled_options()
    raw["vehicle_profiles"][A]["options"][CONF_VEHICLE_PLUG_CHARGE] = value
    assert profiles(raw) == {}


@pytest.mark.parametrize("change", ["same", "source", "remove"])
def test_detection_changes_keep_the_wallbox_switch_and_other_settings(change):
    async def run():
        raw = enabled_options()
        raw["vehicle_profiles"][A]["options"]["vehicle_soc_entity"] = "sensor.car"
        flow = Flow(FakeConfigEntry(options=raw))
        await select_detector(flow)
        if change == "remove":
            result = await flow.async_step_vehicle_detection_source(
                {"vehicle_detection_source": "disabled"}
            )
        else:
            await flow.async_step_vehicle_detection_source(
                {"vehicle_detection_source": "entities"}
            )
            result = await flow.async_step_vehicle_detection_entities(
                {
                    "vehicle_connected_entity": "binary_sensor.plug"
                    if change == "same"
                    else "binary_sensor.new_car",
                    "vehicle_home_entity": "device_tracker.car",
                }
            )
        saved = result["data"]
        assert saved[CONF_VIRTUAL_PLUG_CHARGE] is True
        assert profiles(saved)[A].options == {"vehicle_soc_entity": "sensor.car"}
        assert (profiles(saved)[A].detection is None) is (change == "remove")
        assert profiles(saved)[B] == profiles(raw)[B]

    asyncio.run(run())


def test_selection_and_soc_disable_leave_the_wallbox_switch_alone():
    async def run():
        entry = FakeConfigEntry(options=enabled_options())
        entry.options["vehicle_profiles"][A]["options"]["vehicle_soc_entity"] = "sensor.car"
        flow = Flow(entry)
        await open_profile(flow)
        await flow.async_step_vehicle_battery()
        saved = (await flow.async_step_vehicle_disable())["data"]
        assert saved[CONF_VIRTUAL_PLUG_CHARGE] is True
        entry.options = saved
        await flow.async_step_vehicle()
        selected = (
            await flow.async_step_vehicle_profile_select({"vehicle_profile_id": B})
        )["data"]
        assert selected[CONF_VIRTUAL_PLUG_CHARGE] is True
        assert profiles(selected)[A].detection == profiles(saved)[A].detection

    asyncio.run(run())
