"""Automatic selection is opt-in and requires independently configured evidence."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    VehicleDetectionConfig,
    profiles,
    vehicle_options,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources import provider_identity
from tests.ha_fakes import FakeConfigEntry
from tests.test_options_flow import fields
from tests.test_vehicle_profile_options import Flow
from tests.test_vehicle_profiles import A, B, profile_options


def detected_options(*, source="entities", active=A):
    raw = profile_options(active=active)
    raw["vehicle_profiles"][A]["detection"] = (
        VehicleDetectionConfig("profile")
        if source == "profile"
        else VehicleDetectionConfig(
            "entities", "binary_sensor.plug", "device_tracker.car"
        )
    ).as_dict()
    return raw


@pytest.mark.parametrize("source", ["profile", "entities"])
def test_detector_metadata_roundtrips_without_enabling_automatic_or_vehicle_features(
    source,
):
    raw = detected_options(source=source)
    profile = profiles(raw)[A]
    assert profile.detection.source == source
    assert profile.as_dict() == raw["vehicle_profiles"][A]
    assert profile.options == {}
    assert "vehicle_auto_selection" not in raw
    assert "detection" not in vehicle_options(raw)
    profile.as_dict()["detection"]["source"] = "other"
    assert profiles(raw)[A].detection.source == source


@pytest.mark.parametrize(
    "detection",
    [
        None,
        [],
        {},
        {"source": "unknown"},
        {"source": "profile", "home_entity": "person.owner"},
        {"source": "entities"},
        {
            "source": "entities",
            "connected_entity": "binary_sensor.plug",
            "home_entity": None,
        },
        {
            "source": "entities",
            "connected_entity": "sensor.plug",
            "home_entity": "person.owner",
        },
        {
            "source": "entities",
            "connected_entity": "binary_sensor.plug",
            "home_entity": "sensor.home",
        },
        {
            "source": "entities",
            "connected_entity": "binary_sensor.plug",
            "home_entity": "binary_sensor.plug",
        },
        {
            "source": "entities",
            "connected_entity": "binary_sensor." + "a" * 256,
            "home_entity": "person.owner",
        },
        {
            "source": "entities",
            "connected_entity": "binary_sensor.plug",
            "home_entity": "person.car.owner",
        },
    ],
)
def test_invalid_detector_cannot_leave_a_profile_eligible_for_automatic_selection(
    detection,
):
    raw = profile_options()
    raw["vehicle_profiles"][A]["detection"] = detection
    assert profiles(raw) == {}
    assert vehicle_options(raw)["timed_goal_enabled"] is False


def test_manual_profile_cannot_claim_an_automatic_provider_source():
    raw = detected_options(source="profile")
    raw["vehicle_profiles"][A].update(provider="none", device_id="")
    assert profiles(raw) == {}


async def select_detector(flow, profile=A):
    menu = await flow.async_step_vehicle_detection()
    assert menu["menu_options"] == [
        "vehicle_detection_mode",
        "vehicle_detection_profile",
        "back",
    ]
    return await flow.async_step_vehicle_detection_profile(
        {"vehicle_profile_id": profile}
    )


def test_each_manual_signal_pair_is_saved_only_to_its_profile_without_enabling_automatic():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        original = deepcopy(entry.options)
        flow = Flow(entry)
        form = await select_detector(flow, B)
        assert fields(form)["vehicle_detection_source"].config["options"] == [
            "disabled",
            "entities",
        ]
        form = await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "entities"}
        )
        assert set(fields(form)) == {"vehicle_connected_entity", "vehicle_home_entity"}
        assert fields(form)["vehicle_connected_entity"].config["domain"] == [
            "binary_sensor", "sensor",
        ]
        saved = (
            await flow.async_step_vehicle_detection_entities(
                {
                    "vehicle_connected_entity": "binary_sensor.second_plug",
                    "vehicle_home_entity": "person.owner",
                }
            )
        )["data"]
        assert profiles(saved)[B].detection == VehicleDetectionConfig(
            "entities", "binary_sensor.second_plug", "person.owner"
        )
        assert profiles(saved)[B].options == profiles(original)[B].options
        assert profiles(saved)[A] == profiles(original)[A]
        assert saved["active_vehicle_profile"] == A
        assert "vehicle_auto_selection" not in saved
        assert "timed_goal_reset_pending" not in saved
        assert entry.options == original

    asyncio.run(run())


def test_detection_mode_requires_explicit_boolean_and_configured_evidence():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await flow.async_step_vehicle_detection()
        for value in [None, "yes", 1, []]:
            invalid = await flow.async_step_vehicle_detection_mode(
                {"vehicle_auto_selection": value}
            )
            assert (
                invalid["errors"]["vehicle_auto_selection"]
                == "vehicle_detection_source_invalid"
            )
        missing = await flow.async_step_vehicle_detection_mode(
            {"vehicle_auto_selection": True}
        )
        assert missing["step_id"] == "vehicle_detection_source"
        assert missing["errors"] == {}
        assert not entry.options.get("vehicle_auto_selection")
        disabled = (
            await flow.async_step_vehicle_detection_mode(
                {"vehicle_auto_selection": False}
            )
        )["data"]
        assert disabled == {**entry.options, "vehicle_auto_selection": False}
        entry.options = detected_options()
        await flow.async_step_vehicle_detection()
        enabled = (
            await flow.async_step_vehicle_detection_mode(
                {"vehicle_auto_selection": True}
            )
        )["data"]
        assert enabled == {**entry.options, "vehicle_auto_selection": True}

    asyncio.run(run())


@pytest.mark.parametrize("active", [A, None])
@pytest.mark.parametrize("source", ["profile", "entities"])
def test_enable_detection_guides_missing_sources_and_saves_atomically(active, source):
    async def run():
        entry = FakeConfigEntry(options=profile_options(active=active))
        original = deepcopy(entry.options)
        flow = Flow(entry)
        flow._vehicle_capabilities = lambda _: frozenset({"detection"}) if source == "profile" else frozenset()
        await flow.async_step_vehicle_detection()
        mode = await flow.async_step_vehicle_detection_mode()
        assert mode["last_step"] is False
        form = await flow.async_step_vehicle_detection_mode({"vehicle_auto_selection": True})
        if active is None:
            assert form["step_id"] == "vehicle_detection_profile"
            assert form["errors"] == {}
            form = await flow.async_step_vehicle_detection_profile({"vehicle_profile_id": A})
        assert form["step_id"] == "vehicle_detection_source"
        assert form["description_placeholders"] == {"vehicle": "Car A"}
        assert form["data_schema"]({})["vehicle_detection_source"] == source
        assert entry.options == original
        result = await flow.async_step_vehicle_detection_source({"vehicle_detection_source": source})
        if source == "entities":
            assert result["step_id"] == "vehicle_detection_entities"
            invalid = await flow.async_step_vehicle_detection_entities({"vehicle_connected_entity": "binary_sensor.plug"})
            assert invalid["errors"] == {"vehicle_home_entity": "vehicle_detection_entity_invalid"}
            assert entry.options == original
            result = await flow.async_step_vehicle_detection_entities({
                "vehicle_connected_entity": "binary_sensor.plug", "vehicle_home_entity": "device_tracker.car",
            })
        assert result["type"] == "create_entry"
        assert result["data"]["vehicle_auto_selection"] is True
        assert profiles(result["data"])[A].detection.source == source
        assert result["data"]["active_vehicle_profile"] == active
        assert result["data"]["vehicle_profiles"][B] == original["vehicle_profiles"][B]
        assert entry.options == original

    asyncio.run(run())


@pytest.mark.parametrize("cancel_at", ["source", "entities"])
def test_cancel_guided_detection_preserves_manual_mode_and_all_profile_data(cancel_at):
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        original = deepcopy(entry.options)
        flow = Flow(entry)
        await flow.async_step_vehicle_detection()
        await flow.async_step_vehicle_detection_mode({"vehicle_auto_selection": True})
        if cancel_at == "entities":
            await flow.async_step_vehicle_detection_source({"vehicle_detection_source": "entities"})
        assert (await flow.async_step_back())["step_id"] == "init"
        assert entry.options == original
        # A subsequent manual source edit must not reuse the abandoned intent.
        await select_detector(flow)
        await flow.async_step_vehicle_detection_source({"vehicle_detection_source": "entities"})
        saved = (await flow.async_step_vehicle_detection_entities({
            "vehicle_connected_entity": "binary_sensor.plug", "vehicle_home_entity": "device_tracker.car",
        }))["data"]
        assert "vehicle_auto_selection" not in saved

    asyncio.run(run())


def test_declining_source_during_guided_setup_does_not_enable_automatic_mode():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await flow.async_step_vehicle_detection()
        await flow.async_step_vehicle_detection_mode({"vehicle_auto_selection": True})
        saved = (await flow.async_step_vehicle_detection_source({"vehicle_detection_source": "disabled"}))["data"]
        assert saved["vehicle_auto_selection"] is False
        assert profiles(saved)[A].detection is None
        assert profiles(saved)[B] == profiles(entry.options)[B]

    asyncio.run(run())


def test_profile_source_is_capability_checked_on_selection_and_save():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        await select_detector(flow)
        unsupported = await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "profile"}
        )
        assert (
            unsupported["errors"]["vehicle_detection_source"]
            == "vehicle_capability_unavailable"
        )
        invalid = await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "anything"}
        )
        assert (
            invalid["errors"]["vehicle_detection_source"]
            == "vehicle_detection_source_invalid"
        )
        flow._vehicle_capabilities = lambda _: frozenset({"detection"})
        form = await flow.async_step_vehicle_detection_source()
        assert fields(form)["vehicle_detection_source"].config["options"] == [
            "disabled",
            "profile",
            "entities",
        ]
        saved = (
            await flow.async_step_vehicle_detection_source(
                {"vehicle_detection_source": "profile"}
            )
        )["data"]
        assert profiles(saved)[A].detection == VehicleDetectionConfig("profile")
        assert profiles(saved)[A].options == {}
        assert "vehicle_auto_selection" not in saved

    asyncio.run(run())


@pytest.mark.parametrize(
    "home", ["person.driver", "device_tracker.car", "binary_sensor.car_home"]
)
def test_existing_entity_sources_can_be_edited_and_disabled_without_changing_other_features(
    home,
):
    async def run():
        entry = FakeConfigEntry(options=detected_options())
        entry.options["vehicle_auto_selection"] = True
        flow = Flow(entry)
        await select_detector(flow)
        form = await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "entities"}
        )
        assert set(fields(form)) == {"vehicle_connected_entity", "vehicle_home_entity"}
        saved = (
            await flow.async_step_vehicle_detection_entities(
                {
                    "vehicle_connected_entity": "binary_sensor.plug",
                    "vehicle_home_entity": home,
                }
            )
        )["data"]
        assert profiles(saved)[A].detection.home_entity == home
        assert saved["vehicle_auto_selection"] is True
        entry.options = saved
        await select_detector(flow)
        cleared = (
            await flow.async_step_vehicle_detection_source(
                {"vehicle_detection_source": "disabled"}
            )
        )["data"]
        assert profiles(cleared)[A].detection is None
        assert profiles(cleared)[A].options == profiles(saved)[A].options
        assert cleared["vehicle_auto_selection"] is True

    asyncio.run(run())


@pytest.mark.parametrize(
    ("connected", "home", "key", "error"),
    [
        (
            None,
            "person.driver",
            "vehicle_connected_entity",
            "vehicle_detection_entity_invalid",
        ),
        (
            "switch.plug",
            "person.driver",
            "vehicle_connected_entity",
            "vehicle_detection_entity_invalid",
        ),
        (
            "binary_sensor.plug",
            "sensor.home",
            "vehicle_home_entity",
            "vehicle_detection_entity_invalid",
        ),
        (
            "binary_sensor.plug",
            "binary_sensor.plug",
            "vehicle_home_entity",
            "vehicle_detection_entities_distinct",
        ),
    ],
)
def test_detector_entity_inputs_require_two_distinct_semantically_supported_signals(
    connected, home, key, error
):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        await select_detector(flow)
        await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "entities"}
        )
        form = await flow.async_step_vehicle_detection_entities(
            {
                "vehicle_connected_entity": connected,
                "vehicle_home_entity": home,
            }
        )
        assert form["errors"][key] == error
        assert profiles(flow.config_entry.options)[A].detection is None

    asyncio.run(run())


def test_a_text_sensor_cable_signal_needs_its_connected_states():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        reported = {"sensor.port": SimpleNamespace(
            state="DISCONNECTED", attributes={"options": ["CONNECTED", "DISCONNECTED", 3]},
        )}
        flow.hass.states = SimpleNamespace(get=reported.get)
        await select_detector(flow)
        await flow.async_step_vehicle_detection_source({"vehicle_detection_source": "entities"})
        form = await flow.async_step_vehicle_detection_entities({
            "vehicle_connected_entity": "sensor.port", "vehicle_home_entity": "person.owner",
        })
        assert form["step_id"] == "vehicle_detection_connected_states"
        assert form["description_placeholders"]["entity"] == "sensor.port"
        selector = fields(form)["vehicle_connected_states"]
        assert selector.config["options"] == ["CONNECTED", "DISCONNECTED"]
        assert selector.config["multiple"]
        assert selector.config["custom_value"]
        for value in ([], ["unknown"], ["a", "a"], [str(n) for n in range(9)], "CONNECTED"):
            form = await flow.async_step_vehicle_detection_connected_states(
                {"vehicle_connected_states": value}
            )
            assert form["errors"] == {"vehicle_connected_states": "vehicle_connected_states_invalid"}
        saved = (await flow.async_step_vehicle_detection_connected_states(
            {"vehicle_connected_states": [" CONNECTED", "LOCKED"]}
        ))["data"]
        detection = profiles(saved)[A].detection
        assert detection == VehicleDetectionConfig(
            "entities", "sensor.port", "person.owner", ("CONNECTED", "LOCKED"),
        )
        assert saved["vehicle_profiles"][A]["detection"]["connected_states"] == ["CONNECTED", "LOCKED"]

        # Editing keeps the chosen states, even ones the sensor does not list.
        entry.options = saved
        flow = Flow(entry)
        flow.hass.states = SimpleNamespace(get={}.get)
        await select_detector(flow)
        await flow.async_step_vehicle_detection_source({"vehicle_detection_source": "entities"})
        form = await flow.async_step_vehicle_detection_entities({
            "vehicle_connected_entity": "sensor.port", "vehicle_home_entity": "person.owner",
        })
        assert fields(form)["vehicle_connected_states"].config["options"] == ["CONNECTED", "LOCKED"]

    asyncio.run(run())


def test_connected_states_step_requires_a_chosen_sensor():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        assert (await flow.async_step_vehicle_detection_connected_states())["step_id"] == "init"

    asyncio.run(run())


@pytest.mark.parametrize("detection", [
    {"source": "entities", "connected_entity": "sensor.port", "home_entity": "person.owner"},
    {"source": "entities", "connected_entity": "sensor.port", "home_entity": "person.owner",
     "connected_states": ["unavailable"]},
    {"source": "entities", "connected_entity": "binary_sensor.plug", "home_entity": "person.owner",
     "connected_states": ["on"]},
    {"source": "entities", "connected_entity": "binary_sensor.plug", "home_entity": "sensor.home"},
])
def test_stored_cable_states_must_match_the_signal_type(detection):
    raw = profile_options()
    raw["vehicle_profiles"][A]["detection"] = detection
    assert A not in profiles(raw)


def test_detector_profile_choice_and_stale_draft_cannot_configure_the_wrong_car():
    async def run():
        entry = FakeConfigEntry(options=profile_options())
        flow = Flow(entry)
        await flow.async_step_vehicle_detection()
        assert (await flow.async_step_vehicle_detection_profile())[
            "step_id"
        ] == "vehicle_detection_profile"
        for invalid in [None, "unknown", []]:
            form = await flow.async_step_vehicle_detection_profile(
                {"vehicle_profile_id": invalid}
            )
            assert form["errors"]["vehicle_profile_id"] == "vehicle_profile_required"
        await flow.async_step_vehicle_detection_profile({"vehicle_profile_id": A})
        await flow.async_step_vehicle_detection_source(
            {"vehicle_detection_source": "entities"}
        )
        entry.options["vehicle_profiles"][A]["device_id"] = "another-car"
        result = await flow.async_step_vehicle_detection_entities(
            {
                "vehicle_connected_entity": "binary_sensor.plug",
                "vehicle_home_entity": "person.driver",
            }
        )
        assert result["step_id"] == "options_changed"
        assert profiles(entry.options)[A].detection is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "changed",
    ["active_vehicle_profile", "vehicle_profile_selected_at", "vehicle_auto_selection"],
)
def test_stale_automatic_mode_form_cannot_override_a_more_recent_vehicle_selection(
    changed,
):
    async def run():
        entry = FakeConfigEntry(options=detected_options())
        flow = Flow(entry)
        await flow.async_step_vehicle_detection()
        entry.options[changed] = {
            "active_vehicle_profile": B,
            "vehicle_profile_selected_at": 1234,
            "vehicle_auto_selection": False,
        }[changed]
        form = await flow.async_step_vehicle_detection_mode(
            {"vehicle_auto_selection": True}
        )
        assert form["step_id"] == "options_changed"
        assert not entry.options.get("vehicle_auto_selection")

    asyncio.run(run())


@pytest.mark.parametrize(
    "step",
    [
        "vehicle_detection_mode",
        "vehicle_detection_profile",
        "vehicle_detection_source",
        "vehicle_detection_entities",
    ],
)
def test_detector_steps_cannot_skip_their_section_or_profile(step):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        assert (await getattr(flow, "async_step_" + step)())["step_id"] == "init"
        if step in ("vehicle_detection_source", "vehicle_detection_entities"):
            await flow.async_step_vehicle_detection()
            assert (await getattr(flow, "async_step_" + step)())["step_id"] == "init"
        with pytest.raises(RuntimeError, match="incomplete vehicle detection"):
            flow._save_detection(None)

    asyncio.run(run())


def test_entities_require_the_explicit_source_step_and_a_still_existing_profile():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        await select_detector(flow)
        assert (await flow.async_step_vehicle_detection_entities())["step_id"] == "init"
        await select_detector(flow)
        flow._draft.values["vehicle_profiles"].pop(A)
        assert (await flow.async_step_vehicle_detection_source())["step_id"] == "init"

    asyncio.run(run())


@pytest.mark.parametrize("clear", [False, True])
def test_manual_profile_selection_explicitly_overrides_automatic_mode(clear):
    async def run():
        raw = detected_options()
        raw["vehicle_auto_selection"] = True
        flow = Flow(FakeConfigEntry(options=raw))
        await flow.async_step_vehicle_profiles()
        result = (
            await flow.async_step_vehicle_profile_clear()
            if clear
            else await flow.async_step_vehicle_profile_select({"vehicle_profile_id": A})
        )
        assert result["data"]["vehicle_auto_selection"] is False
        assert result["data"]["active_vehicle_profile"] == (None if clear else A)
        assert profiles(result["data"])[A].detection == profiles(raw)[A].detection

    asyncio.run(run())


def test_profile_rename_preserves_detection_but_changed_identity_discards_old_car_signals():
    async def run():
        entry = FakeConfigEntry(options=detected_options())
        flow = Flow(entry)
        entry.options["vehicle_profiles"][A]["identity"] = provider_identity(
            flow.hass, "bavariandata", "car"
        )
        await flow.async_step_vehicle_profiles()
        await flow.async_step_vehicle_profile_edit_select({"vehicle_profile_id": A})
        await flow.async_step_vehicle_profile_edit(
            {"vehicle_profile_name": "Renamed", "vehicle_provider": "bavariandata"}
        )
        saved = (
            await flow.async_step_vehicle_profile_device({"vehicle_device": "car"})
        )["data"]
        assert profiles(saved)[A].detection == profiles(entry.options)[A].detection
        entry.options = saved
        await flow.async_step_vehicle_profiles()
        await flow.async_step_vehicle_profile_edit_select({"vehicle_profile_id": A})
        changed = (
            await flow.async_step_vehicle_profile_edit(
                {"vehicle_profile_name": "Other car", "vehicle_provider": "none"}
            )
        )["data"]
        assert profiles(changed)[A].detection is None
        assert changed["timed_goal_reset_pending"] is True

    asyncio.run(run())
