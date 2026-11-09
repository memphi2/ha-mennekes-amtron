"""Vehicle selection is explicit, scoped, and separate from feature activation."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    LEGACY_PROFILE_ID,
    profiles,
    vehicle_options,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources import provider_identity
from tests.ha_fakes import FakeConfigEntry
from tests.test_bavarian_departure import FakeHass
from tests.test_bavarian_history import VIN
from tests.test_charge_loss_options import Flow as BaseFlow
from tests.test_options_flow import fields
from tests.test_vehicle_profiles import A, B, profile_options


class Flow(BaseFlow):
    def __init__(self, entry):
        super().__init__(entry)
        self.hass = FakeHass(**vars(self.hass))
        descriptors = (
            "vehicle.drivetrain.batteryManagement.header",
            "vehicle.powertrain.electric.battery.stateOfCharge.target",
            "vehicle.drivetrain.batteryManagement.maxEnergy",
            *(f"vehicle.cabin.climate.timers.overwriteTimer.{part}" for part in ("action", "hour", "minute")),
        )
        entries = {
            f"{VIN}_{descriptor}": SimpleNamespace(
                entity_id=f"sensor.test_{index}", device_id="car", disabled_by=None
            )
            for index, descriptor in enumerate(descriptors)
        }
        registry = SimpleNamespace(entries=entries)
        registry.async_get_entity_id = lambda _domain, _platform, unique: (
            entries[unique].entity_id if unique in entries else None
        )
        registry.async_get = lambda entity_id: next(
            (item for item in entries.values() if item.entity_id == entity_id), None
        )
        self.hass.data[er.DATA_REGISTRY] = registry
        self.states: dict[str, State] = {}
        self.hass.states = SimpleNamespace(get=self.states.get)


async def open_profile(flow, key=A):
    """Open one car's settings area by area, outside the guided setup."""
    await flow.async_step_vehicle()
    flow._profile_target = key
    menu = await flow.async_step_vehicle_profile()
    assert menu["step_id"] == "vehicle_profile"
    return menu


def test_profile_add_preserves_legacy_mixed_sources_and_uses_what_the_car_reports():
    async def run():
        old = {
            "vehicle_soc_entity": "sensor.manual",
            "timed_bavarian_device": "car",
            "charge_loss_bavarian_device": "other",
            "charging_price": 0.4,
        }
        flow = Flow(FakeConfigEntry(options=old))
        menu = await flow.async_step_vehicle()
        assert menu["menu_options"] == ["vehicle_profile_add", "vehicle_settings", "back"]
        form = await flow.async_step_vehicle_profile_add(
            {
                "vehicle_profile_name": "My BMW",
                "vehicle_provider": "bavariandata",
            }
        )
        assert set(fields(form)) == {"vehicle_device"}
        assert (
            await flow.async_step_vehicle_profile_device({"vehicle_device": "invalid"})
        )["errors"]
        flow.states["sensor.test_1"] = State("sensor.test_1", "80", {"unit_of_measurement": "%"})
        menu = await flow.async_step_vehicle_profile_device({"vehicle_device": "car"})
        # The guided setup continues with the new car's charge level.
        assert menu["step_id"] == "vehicle_battery"
        saved = (await flow.async_step_wizard_finish())["data"]
        assert saved["active_vehicle_profile"] == LEGACY_PROFILE_ID
        assert "vehicle_profile_selected_at" not in saved
        assert vehicle_options(saved)["vehicle_soc_entity"] == "sensor.manual"
        assert vehicle_options(saved)["timed_bavarian_device"] == "car"
        assert vehicle_options(saved)["charge_loss_bavarian_device"] == "other"
        added = [
            item for item in profiles(saved).values() if item.id != LEGACY_PROFILE_ID
        ]
        assert len(added) == 1
        # A newly linked car uses everything it reports.
        assert added[0].options == {
            "vehicle_soc_use_profile": True, "vehicle_limit_use_profile": True,
            "timed_use_vehicle_capacity": True, "charge_loss_source": "vehicle_history", "charge_loss_vehicle_provider": "profile",
        }
        assert added[0].detection is None
        assert added[0].provider_id == "bavariandata"
        assert not {"vehicle_soc_entity", "timed_bavarian_device"} & saved.keys()
        assert saved["charging_price"] == 0.4

    asyncio.run(run())


def test_first_manual_profile_is_active_but_all_vehicle_features_stay_off():
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_vehicle()
        await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": "Other EV", "vehicle_provider": "none"}
        )
        saved = (await flow.async_step_wizard_finish())["data"]
        assert len(profiles(saved)) == 1
        assert profiles(saved)[saved["active_vehicle_profile"]].options == {}
        assert saved["timed_goal_reset_pending"] is True
        assert saved["vehicle_profile_selected_at"] > 0

    asyncio.run(run())


@pytest.mark.parametrize("name", [None, "", " " * 3, "x" * 81, "car b"])
def test_profile_name_is_required_bounded_and_unique(name):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        await flow.async_step_vehicle()
        result = await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": name, "vehicle_provider": "none"}
        )
        assert (
            result["errors"]["vehicle_profile_name"] == "vehicle_profile_name_invalid"
        )

    asyncio.run(run())


def test_profile_provider_and_catalog_limit_are_validated_before_save():
    async def run():
        raw = profile_options()
        raw["vehicle_profiles"] = {
            f"{index:032x}": {
                "name": str(index),
                "provider": "none",
                "device_id": "",
                "options": {},
            }
            for index in range(8)
        }
        flow = Flow(FakeConfigEntry(options=raw))
        await flow.async_step_vehicle()
        result = await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": "More", "vehicle_provider": "bad"}
        )
        assert result["errors"] == {
            "base": "vehicle_profile_limit",
            "vehicle_provider": "vehicle_provider_invalid",
        }

    asyncio.run(run())


def test_profile_edit_rename_keeps_features_but_changed_vehicle_resets_them():
    async def run():
        entry = FakeConfigEntry(
            options=profile_options(
                feature={"vehicle_soc_use_profile": True, "timed_goal_enabled": True}
            )
        )
        flow = Flow(entry)
        entry.options["vehicle_profiles"][A]["identity"] = provider_identity(
            flow.hass, "bavariandata", "car"
        )
        assert (
            await flow.async_step_vehicle_profile_open({"vehicle_profile_id": "missing"})
        )["errors"]
        await open_profile(flow)
        edit = await flow.async_step_vehicle_profile_edit()
        assert edit["step_id"] == "vehicle_profile_edit"
        await flow.async_step_vehicle_profile_edit(
            {"vehicle_profile_name": "Renamed", "vehicle_provider": "bavariandata"}
        )
        same = (
            await flow.async_step_vehicle_profile_device({"vehicle_device": "car"})
        )["data"]
        assert (
            same["vehicle_profiles"][A]["options"]
            == entry.options["vehicle_profiles"][A]["options"]
        )
        assert "vehicle_profile_selected_at" not in same
        entry.options = same
        await open_profile(flow)
        await flow.async_step_vehicle_profile_edit()
        await flow.async_step_vehicle_profile_edit(
            {"vehicle_profile_name": "Renamed", "vehicle_provider": "bavariandata"}
        )
        changed = (
            await flow.async_step_vehicle_profile_device({"vehicle_device": "own"})
        )["data"]
        # The other car starts over with only what it reports.
        assert changed["vehicle_profiles"][A]["options"] == {
            "charge_loss_source": "vehicle_history", "charge_loss_vehicle_provider": "profile",
        }
        assert changed["timed_goal_reset_pending"] is True
        assert changed["vehicle_profile_selected_at"] > 0

    asyncio.run(run())


def test_profile_select_clear_and_remove_never_auto_choose_another_vehicle():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await flow.async_step_vehicle()
        selected = (
            await flow.async_step_vehicle_profile_select({"vehicle_profile_id": B})
        )["data"]
        assert selected["active_vehicle_profile"] == B
        entry.options = selected
        await flow.async_step_vehicle()
        assert (
            await flow.async_step_vehicle_profile_remove(
                {"vehicle_profile_id": B, "confirm": False}
            )
        )["errors"]["confirm"] == "vehicle_profile_confirm_required"
        removed = (
            await flow.async_step_vehicle_profile_remove(
                {"vehicle_profile_id": B, "confirm": True}
            )
        )["data"]
        assert removed["active_vehicle_profile"] is None
        assert set(profiles(removed)) == {A}
        entry.options = profile_options()
        await flow.async_step_vehicle()
        inactive_removed = (
            await flow.async_step_vehicle_profile_remove(
                {"vehicle_profile_id": B, "confirm": True}
            )
        )["data"]
        assert inactive_removed["active_vehicle_profile"] == A
        entry.options = inactive_removed
        await flow.async_step_vehicle()
        assert (await flow.async_step_vehicle_profile_clear())["data"][
            "active_vehicle_profile"
        ] is None

    asyncio.run(run())


def test_profile_management_conflicts_with_concurrent_feature_edit_and_back_discards():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await flow.async_step_vehicle()
        await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": "Third", "vehicle_provider": "bavariandata"}
        )
        entry.options["vehicle_profiles"][A]["options"]["vehicle_soc_use_profile"] = (
            True
        )
        # The guided setup saves at its end and then sees the other edit.
        assert (
            await flow.async_step_vehicle_profile_device({"vehicle_device": "car"})
        )["step_id"] == "vehicle_battery"
        assert (await flow.async_step_wizard_finish())["step_id"] == "options_changed"
        await flow.async_step_back()
        assert len(profiles(entry.options)) == 2

    asyncio.run(run())


def test_existing_sources_name_cannot_collide_with_preserved_legacy_profile():
    async def run():
        flow = Flow(FakeConfigEntry(options={"vehicle_soc_entity": "sensor.old"}))
        await flow.async_step_vehicle()
        await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": "Existing sources", "vehicle_provider": "none"}
        )
        saved = (await flow.async_step_wizard_finish())["data"]
        assert len(profiles(saved)) == 2
        assert profiles(saved)[LEGACY_PROFILE_ID].name == "Existing sources (1)"

    asyncio.run(run())


@pytest.mark.parametrize(
    "step",
    [
        "vehicle_profile_add",
        "vehicle_profile_edit",
        "vehicle_profile_device",
        "vehicle_profile_select",
        "vehicle_profile_remove",
        "vehicle_profile_clear",
        "vehicle_source",
        "vehicle_profile_soc",
        "loss_profile",
    ],
)
def test_profile_details_cannot_be_entered_without_their_section(step):
    async def run():
        flow = Flow(FakeConfigEntry())
        # Without a section or an opened profile, nothing can be edited.
        assert (await getattr(flow, f"async_step_{step}")())["step_id"] in ("init", "vehicle")

    asyncio.run(run())


def test_profile_features_are_selected_independently_and_saved_into_their_car():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await open_profile(flow)
        assert "vehicle_source" in (await flow.async_step_vehicle_battery())["menu_options"]
        assert set(fields(await flow.async_step_vehicle_source())) == {
            "vehicle_soc_source"
        }
        assert (await flow.async_step_vehicle_source({"vehicle_soc_source": "bad"}))[
            "errors"
        ]
        assert (
            await flow.async_step_vehicle_source({"vehicle_soc_source": "profile"})
        )["step_id"] == "vehicle_profile_soc"
        assert (
            await flow.async_step_vehicle_profile_soc({"use_vehicle_limit": "yes"})
        )["errors"]
        selected = (
            await flow.async_step_vehicle_profile_soc({"use_vehicle_limit": True})
        )["data"]
        assert selected["vehicle_profiles"][A]["options"] == {
            "vehicle_soc_use_profile": True,
            "vehicle_limit_use_profile": True,
        }
        assert selected["vehicle_profiles"][B] == entry.options["vehicle_profiles"][B]
        entry.options = selected
        await flow.async_step_departure()
        form = await flow.async_step_departure_configure(
            {"capacity_source": "profile", "use_vehicle_timer": True}
        )
        assert form["step_id"] == "departure_vehicle_details"
        assert "timed_vehicle_device" not in fields(form)
        saved = (await flow.async_step_departure_vehicle_details({}))["data"]
        options = saved["vehicle_profiles"][A]["options"]
        assert options["timed_use_vehicle_capacity"] is True
        assert options["timed_vehicle_provider"] == "profile"
        assert "timed_vehicle_device" not in options
        entry.options = saved
        assert "loss_profile" in (await flow.async_step_losses())["menu_options"]
        final = (await flow.async_step_loss_profile())["data"]
        assert (
            final["vehicle_profiles"][A]["options"]["charge_loss_vehicle_provider"]
            == "profile"
        )
        assert final["charging_price"] == 0.4

    asyncio.run(run())


def test_manual_entity_override_and_disabled_features_do_not_delete_the_profile():
    async def run():
        entry = FakeConfigEntry(
            options=profile_options(
                feature={
                    "vehicle_soc_use_profile": True,
                    "vehicle_limit_use_profile": True,
                }
            )
        )
        flow = Flow(entry)
        await open_profile(flow)
        await flow.async_step_vehicle_battery()
        assert (await flow.async_step_vehicle_source({"vehicle_soc_source": "entity"}))[
            "step_id"
        ] == "vehicle_soc"
        saved = (
            await flow.async_step_vehicle_soc({"vehicle_soc_entity": "sensor.manual"})
        )["data"]
        assert saved["vehicle_profiles"][A]["options"] == {
            "vehicle_soc_entity": "sensor.manual"
        }
        entry.options = saved
        assert "vehicle_disable" in (await flow.async_step_vehicle_battery())["menu_options"]
        disabled = (await flow.async_step_vehicle_disable())["data"]
        assert disabled["vehicle_profiles"][A]["options"] == {
            "timed_goal_enabled": False
        }
        assert disabled["active_vehicle_profile"] == A
        assert disabled["vehicle_profiles"][B] == entry.options["vehicle_profiles"][B]
        entry.options = disabled
        assert "vehicle_disable" not in (await flow.async_step_vehicle_battery())["menu_options"]

    asyncio.run(run())


@pytest.mark.parametrize("configured", [False, True])
def test_profile_percentage_removal_is_offered_only_for_enabled_soc(configured):
    async def run():
        entry = FakeConfigEntry(options=profile_options(feature={
            "vehicle_soc_use_profile": configured,
            "vehicle_limit_use_profile": configured,
        }))
        flow = Flow(entry)
        await open_profile(flow)
        menu = (await flow.async_step_vehicle_battery())["menu_options"]
        assert menu[-1] == "vehicle_profile"
        assert "vehicle_source" in menu
        assert ("vehicle_disable" in menu) is configured

    asyncio.run(run())


def test_vehicle_features_need_an_opened_profile_but_not_a_selected_car():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(active=None)))
        assert (await flow.async_step_vehicle())["menu_options"] == [
            "vehicle_profile_add",
            "vehicle_profile_open",
            "vehicle_profile_select",
            "vehicle_profile_remove",
            "vehicle_profile_clear",
            "back",
        ]
        for step in ("vehicle_battery", "departure", "losses", "vehicle_profile_edit"):
            assert (await getattr(flow, f"async_step_{step}")())["step_id"] == "vehicle"
        menu = await open_profile(flow, B)
        assert menu["description_placeholders"] == {"vehicle": "Car B"}
        assert menu["menu_options"] == [
            "vehicle_profile_edit",
            "vehicle_battery",
            "departure",
            "losses",
            "vehicle_detection_signals",
            "vehicle",
        ]
        assert (await flow.async_step_departure())["step_id"] == "departure"
        assert (await flow.async_step_losses())["menu_options"][-1] == "vehicle_profile"
        await flow.async_step_back()
        with pytest.raises(RuntimeError, match="incomplete profile"):
            await flow._save_profile("car")

    asyncio.run(run())


def test_missing_vehicle_capability_cannot_enable_profile_features():
    async def run():
        entry = FakeConfigEntry(
            options=profile_options(feature={"vehicle_soc_entity": "sensor.manual"})
        )
        flow = Flow(entry)
        flow.hass.data[er.DATA_REGISTRY].entries.clear()
        await open_profile(flow)
        await flow.async_step_vehicle_battery()
        source = await flow.async_step_vehicle_source({"vehicle_soc_source": "profile"})
        assert source["errors"]["vehicle_soc_source"] == "vehicle_capability_unavailable"
        assert fields(source)["vehicle_soc_source"].config["options"] == ["entity"]
        await flow.async_step_departure()
        unavailable = await flow.async_step_departure_configure(
            {"capacity_source": "profile"}
        )
        assert (
            unavailable["errors"]["capacity_source"] == "vehicle_capability_unavailable"
        )
        assert fields(unavailable)["capacity_source"].config["options"] == [
            "fixed",
            "entity",
        ]
        assert entry.options["vehicle_profiles"][A]["options"] == {
            "vehicle_soc_entity": "sensor.manual"
        }

    asyncio.run(run())


def test_changed_vehicle_identity_cannot_enable_soc_or_history_on_old_profile():
    async def run():
        raw = profile_options()
        raw["vehicle_profiles"][A]["identity"] = "bavariandata:car:previous-vehicle"
        flow = Flow(FakeConfigEntry(options=raw))
        await open_profile(flow)
        await flow.async_step_vehicle_battery()
        source = await flow.async_step_vehicle_source({"vehicle_soc_source": "profile"})
        assert source["errors"]["vehicle_soc_source"] == "vehicle_capability_unavailable"
        assert fields(source)["vehicle_soc_source"].config["options"] == ["entity"]
        await flow.async_step_losses()
        fallback = await flow.async_step_loss_profile()
        assert fallback["type"] == "menu"
        assert fallback["menu_options"] == ["loss_sensor", "vehicle_profile"]

    asyncio.run(run())


def test_device_identity_disappearing_during_selection_cannot_save_a_profile(
    monkeypatch,
):
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_vehicle()
        await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": "BMW", "vehicle_provider": "bavariandata"}
        )
        monkeypatch.setattr(
            "custom_components.mennekes_amtron_rtu._options_profiles.provider_identity",
            lambda *_: None,
        )
        result = await flow.async_step_vehicle_profile_device({"vehicle_device": "car"})
        assert result["errors"]["vehicle_device"] == "vehicle_device_invalid"
        assert flow.config_entry.options == {}

    asyncio.run(run())


def test_manual_profile_does_not_offer_unavailable_manufacturer_controls():
    async def run():
        flow = Flow(
            FakeConfigEntry(
                options=profile_options(
                    provider="none", feature={"vehicle_soc_entity": "sensor.manual"}
                )
            )
        )
        await open_profile(flow)
        await flow.async_step_departure()
        form = await flow.async_step_departure_configure()
        assert "use_vehicle_timer" not in fields(form)
        assert fields(form)["capacity_source"].config["options"] == ["fixed", "entity"]
        assert (await flow.async_step_losses())["menu_options"] == [
            "loss_sensor",
            "vehicle_profile",
        ]

    asyncio.run(run())


def test_stale_or_empty_profile_menus_return_to_the_vehicle_overview():
    async def run():
        empty = Flow(FakeConfigEntry(options={"vehicle_profiles": {}}))
        assert (await empty.async_step_vehicle())["menu_options"] == [
            "vehicle_profile_add",
            "back",
        ]
        assert (await empty.async_step_vehicle_profile_open())["step_id"] == "vehicle"

        flow = Flow(FakeConfigEntry(options=profile_options()))
        assert (await flow.async_step_vehicle_profile_select())["step_id"] == "init"
        await flow.async_step_vehicle()
        result = await flow.async_step_vehicle_profile_select({"vehicle_profile_id": "9" * 32})
        assert result["errors"] == {"vehicle_profile_id": "vehicle_profile_required"}
        flow._profile_target = "9" * 32
        assert (await flow.async_step_vehicle_profile())["step_id"] == "vehicle"

        await open_profile(flow)
        assert (await flow.async_step_vehicle_profile_edit())["step_id"] == "vehicle_profile_edit"
        flow._details = {}
        result = await flow.async_step_vehicle_profile_edit({"vehicle_profile_name": "Renamed"})
        assert result["step_id"] == "vehicle"

    asyncio.run(run())
