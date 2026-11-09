"""Adding or opening a car guides through all of its settings, saved at the end."""

import asyncio

from custom_components.mennekes_amtron_rtu.vehicle_profiles import profiles
from tests.ha_fakes import FakeConfigEntry
from tests.test_vehicle_profile_options import Flow
from tests.test_vehicle_profiles import A, B, profile_options


async def opened(flow, key=A, name="Car A"):
    await flow.async_step_vehicle()
    await flow.async_step_vehicle_profile_open({"vehicle_profile_id": key})
    return await flow.async_step_vehicle_profile_edit(
        {"vehicle_profile_name": name, "vehicle_provider": "none"}
    )


def test_every_area_is_offered_in_order_and_saved_once_at_the_end():
    async def run():
        entry = FakeConfigEntry(options=profile_options(provider="none"))
        flow = Flow(entry)
        menu = await opened(flow)
        assert menu["step_id"] == "vehicle_battery"
        assert menu["menu_options"][-2:] == ["wizard_next", "wizard_finish"]
        # Without a charge level, departure planning only explains that.
        menu = await flow.async_step_wizard_next()
        assert menu["step_id"] == "departure_requires_vehicle"
        assert menu["menu_options"] == ["wizard_next", "wizard_finish"]
        menu = await flow.async_step_wizard_next()
        assert menu["step_id"] == "losses"
        form = await flow.async_step_wizard_next()
        assert form["step_id"] == "vehicle_detection_source"
        # Detection stays optional while passing through.
        assert form["data_schema"]({})["vehicle_detection_source"] == "disabled"
        result = await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "disabled"}
        )
        assert result["type"] == "create_entry"
        assert result["data"]["vehicle_profiles"] == entry.options["vehicle_profiles"]

    asyncio.run(run())


def test_changes_elsewhere_are_kept_unless_they_touch_the_same_setting():
    async def run():
        entry = FakeConfigEntry(options=profile_options(provider="none"))
        flow = Flow(entry)
        await opened(flow, name="Renamed")
        # Another car is selected and a price changes meanwhile: both stay.
        entry.options["active_vehicle_profile"] = B
        entry.options.pop("charging_price")
        result = await flow.async_step_wizard_finish()
        assert result["data"]["active_vehicle_profile"] == B
        assert "charging_price" not in result["data"]
        assert profiles(result["data"])[A].name == "Renamed"

        # A removed setting is removed, unless it was changed elsewhere.
        entry = FakeConfigEntry(options={**profile_options(provider="none"), "extra": 1})
        flow = Flow(entry)
        await opened(flow)
        flow._wizard_options.pop("extra")
        assert "extra" not in (await flow.async_step_wizard_finish())["data"]
        flow = Flow(entry)
        await opened(flow, name="Renamed")
        entry.options["vehicle_profiles"][B]["name"] = "Elsewhere"
        assert (await flow.async_step_wizard_finish())["step_id"] == "options_changed"

    asyncio.run(run())


def test_the_wizard_steps_need_a_running_wizard():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(provider="none")))
        assert (await flow.async_step_wizard_next())["step_id"] == "init"
        assert (await flow.async_step_wizard_finish())["step_id"] == "init"
        await opened(flow)
        # Leaving the guided setup discards it.
        await flow.async_step_back()
        assert flow._wizard is None
        assert (await flow.async_step_wizard_finish())["step_id"] == "init"

    asyncio.run(run())


def test_a_section_conflict_inside_the_wizard_ends_it():
    async def run():
        entry = FakeConfigEntry(options=profile_options(provider="none"))
        flow = Flow(entry)
        await opened(flow)
        await flow.async_step_vehicle_soc()
        # The draft was opened from the wizard's options; another edit of the
        # same car's settings inside the wizard makes it stale.
        flow._wizard_options["vehicle_profiles"][A]["options"]["vehicle_soc_entity"] = "sensor.x"
        result = await flow.async_step_vehicle_soc(
            {"vehicle_soc_entity": "sensor.car_soc", "use_vehicle_limit": False}
        )
        assert result["step_id"] == "options_changed"
        assert flow._wizard is None

    asyncio.run(run())
