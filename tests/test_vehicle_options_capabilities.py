"""Offer only available vehicle data and meaningful remove actions."""

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.vehicle_profiles import activate_profile
from tests.ha_fakes import FakeConfigEntry
from tests.test_options_flow import fields
from tests.test_vehicle_profile_options import Flow
from tests.test_vehicle_profiles import A, B, profile_options


@pytest.mark.parametrize("capabilities", [frozenset(), frozenset({"soc"}), frozenset({"soc", "charge_limit"})])
def test_percentage_source_choices_reflect_actual_profile_capabilities(capabilities):
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        flow._vehicle_capabilities = lambda _: capabilities
        await flow.async_step_vehicle()
        form = await flow.async_step_vehicle_source()
        assert form["description_placeholders"] == {"vehicle": "Car A"}
        assert fields(form)["vehicle_soc_source"].config["options"] == (
            ["profile", "entity"] if "soc" in capabilities else ["entity"]
        )
        if "soc" not in capabilities:
            assert form["data_schema"]({})["vehicle_soc_source"] == "entity"
            return
        result = await flow.async_step_vehicle_source({"vehicle_soc_source": "profile"})
        if "charge_limit" in capabilities:
            assert set(fields(result)) == {"use_vehicle_limit"}
            result = await flow.async_step_vehicle_profile_soc({"use_vehicle_limit": True})
        # A profile without a maximum needs no empty confirmation form.
        assert result["type"] == "create_entry"
        assert result["data"]["vehicle_profiles"][A]["options"] == {
            "vehicle_soc_use_profile": True,
            "vehicle_limit_use_profile": "charge_limit" in capabilities,
        }

    asyncio.run(run())


@pytest.mark.parametrize("remaining", [frozenset(), frozenset({"soc"})])
@pytest.mark.parametrize("user_input", [None, {"use_vehicle_limit": True}])
def test_disappearing_capability_rechecks_profile_form_before_save(remaining, user_input):
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await flow.async_step_vehicle()
        await flow.async_step_vehicle_source({"vehicle_soc_source": "profile"})
        flow._vehicle_capabilities = lambda _: remaining
        form = await flow.async_step_vehicle_profile_soc(user_input)
        assert form["step_id"] == "vehicle_source"
        assert form["errors"] == {}
        assert fields(form)["vehicle_soc_source"].config["options"] == (
            ["profile", "entity"] if "soc" in remaining else ["entity"]
        )
        assert entry.options["vehicle_profiles"][A]["options"] == {}

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_departure_remove_action_is_offered_only_when_enabled(enabled):
    async def run():
        entry = FakeConfigEntry(options=profile_options(feature={
            "vehicle_soc_entity": "sensor.soc", "timed_goal_enabled": enabled,
        }))
        flow = Flow(entry)
        menu = await flow.async_step_departure()
        assert menu["description_placeholders"] == {"vehicle": "Car A"}
        assert ("departure_disable" in menu["menu_options"]) is enabled
        assert "departure_configure" in menu["menu_options"]
        if enabled:
            entry.options = (await flow.async_step_departure_disable())["data"]
            assert "departure_disable" not in (await flow.async_step_departure())["menu_options"]

    asyncio.run(run())


@pytest.mark.parametrize("section", ["vehicle", "departure"])
def test_open_form_keeps_its_original_vehicle_name_after_automatic_selection(section):
    async def run():
        entry = FakeConfigEntry(options=profile_options(feature={
            "vehicle_soc_entity": "sensor.original", "timed_goal_enabled": True,
        }))
        flow = Flow(entry)
        await getattr(flow, f"async_step_{section}")()
        entry.options = activate_profile(entry.options, B)
        if section == "vehicle":
            form = await flow.async_step_vehicle_soc()
            result = await flow.async_step_vehicle_soc({"vehicle_soc_entity": "sensor.changed"})
        else:
            form = await flow.async_step_departure_configure()
            await flow.async_step_departure_configure({"capacity_source": "fixed"})
            result = await flow.async_step_departure_capacity_fixed({"timed_capacity_kwh": 80})
        assert form["description_placeholders"] == {"vehicle": "Car A"}
        assert result["step_id"] == "options_changed"
        assert entry.options["active_vehicle_profile"] == B
        assert entry.options["vehicle_profiles"][B]["options"] == {"vehicle_soc_entity": "sensor.other"}
        # Discarding the old draft allows the next section to describe B.
        await flow.async_step_back()
        assert flow._vehicle_placeholders() == {"vehicle": "Car B"}

    asyncio.run(run())
