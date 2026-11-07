"""Profiles keep independently optional vehicle settings and identities isolated."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu._options_draft import (
    OptionsChangedError,
    OptionsDraft,
)
from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    LEGACY_PROFILE_ID,
    VehicleProfile,
    activate_profile,
    active_profile,
    profile_list,
    profiles,
    vehicle_options,
    vehicle_soc_is_configured,
)

A, B = "1" * 32, "2" * 32


def profile_options(*, active=A, provider="bavariandata", feature=None):
    return {
        "charging_price": 0.4,
        "vehicle_profiles": {
            A: VehicleProfile(
                A, "Car A", provider, "car" if provider != "none" else "", feature or {}
            ).as_dict(),
            B: VehicleProfile(
                B, "Car B", "none", "", {"vehicle_soc_entity": "sensor.other"}
            ).as_dict(),
        },
        "active_vehicle_profile": active,
    }


def test_vehicle_profile_projection_preserves_global_settings_and_does_not_enable_features():
    raw = profile_options()
    effective = vehicle_options(raw)
    assert effective == {
        "charging_price": 0.4,
        "vehicle_profile_id": A,
        "vehicle_provider": "bavariandata",
        "vehicle_device": "car",
        "vehicle_source_identity": "",
    }
    assert not vehicle_soc_is_configured(effective)
    assert [profile.id for profile in profile_list(raw)] == [A, B]
    assert active_profile(raw).id == A
    copied = profiles(raw)[B].as_dict()
    copied["options"]["vehicle_soc_entity"] = "sensor.changed"
    assert raw["vehicle_profiles"][B]["options"]["vehicle_soc_entity"] == "sensor.other"
    raw["vehicle_soc_entity"] = "sensor.obsolete"
    assert "vehicle_soc_entity" not in vehicle_options(raw)


@pytest.mark.parametrize(
    "catalog",
    [
        None,
        [],
        {"bad": {}},
        {True: {}},
        {A: None},
        {A: {}},
        {A: {"name": "", "provider": "none", "device_id": "", "options": {}}},
        {A: {"name": "A", "provider": "", "device_id": "", "options": {}}},
        {A: {"name": "A", "provider": "bavariandata", "device_id": "", "options": {}}},
        {A: {"name": "A", "provider": "none", "device_id": "", "options": []}},
        {
            A: {
                "name": "A",
                "provider": "none",
                "device_id": "",
                "options": {"charging_price": 10},
            }
        },
    ],
)
def test_malformed_profile_catalog_cannot_revive_legacy_vehicle_settings(catalog):
    raw = {
        "vehicle_profiles": catalog,
        "active_vehicle_profile": A,
        "vehicle_soc_entity": "sensor.old",
        "charging_price": 0.4,
    }
    assert profiles(raw) == {}
    assert active_profile(raw) is None
    assert vehicle_options(raw) == {"charging_price": 0.4, "timed_goal_enabled": False}


def test_profile_catalog_is_bounded_and_names_are_distinct():
    raw = profile_options()
    raw["vehicle_profiles"][B]["name"] = "CAR A"
    assert profiles(raw) == {}
    raw["vehicle_profiles"] = {
        f"{index:032x}": VehicleProfile(
            f"{index:032x}", str(index), "none", "", {}
        ).as_dict()
        for index in range(9)
    }
    assert profiles(raw) == {}
    raw["vehicle_profiles"].pop(LEGACY_PROFILE_ID)
    assert len(profiles(raw)) == 8


def test_legacy_configuration_and_explicit_manual_profile_remain_manual():
    raw = {"vehicle_soc_entity": "sensor.manual", "timed_bavarian_device": "old-car"}
    assert vehicle_options(raw) == raw
    projected = vehicle_options(
        profile_options(
            provider="none",
            feature={
                "vehicle_soc_entity": "sensor.manual",
                "vehicle_soc_use_profile": True,
                "vehicle_limit_use_profile": True,
                "timed_use_vehicle_capacity": True,
            },
        )
    )
    assert projected["vehicle_soc_entity"] == "sensor.manual"
    assert (
        not {
            "vehicle_soc_use_profile",
            "vehicle_limit_use_profile",
            "timed_use_vehicle_capacity",
        }
        & projected.keys()
    )
    assert vehicle_soc_is_configured(projected)


@pytest.mark.parametrize(
    ("options", "configured"),
    [
        ({}, False),
        ({"vehicle_soc_entity": "sensor.manual"}, True),
        ({"vehicle_soc_use_profile": True}, False),
        (
            {
                "vehicle_soc_use_profile": True,
                "vehicle_provider": "none",
                "vehicle_device": "car",
            },
            False,
        ),
        (
            {
                "vehicle_soc_use_profile": True,
                "vehicle_provider": "bavariandata",
                "vehicle_device": "car",
            },
            True,
        ),
    ],
)
def test_soc_feature_requires_its_own_explicit_configuration(options, configured):
    assert vehicle_soc_is_configured(options) is configured


def test_profile_switch_is_explicit_atomic_and_marks_old_trip_unusable():
    raw = profile_options()
    with patch(
        "custom_components.mennekes_amtron_rtu.vehicle_profiles.time.time",
        return_value=1000,
    ):
        assert activate_profile(raw, A) == raw
        switched = activate_profile(raw, B)
        assert switched["active_vehicle_profile"] == B
        assert switched["timed_goal_reset_pending"] is True
        assert switched["vehicle_profile_selected_at"] == 1000
        assert switched["vehicle_profiles"] == raw["vehicle_profiles"]
        cleared = activate_profile(switched, None)
        assert active_profile(cleared) is None
        assert "vehicle_soc_entity" not in vehicle_options(cleared)
    assert raw["active_vehicle_profile"] == A
    with pytest.raises(ValueError, match="unknown vehicle profile"):
        activate_profile(raw, "missing")


def test_profile_draft_merges_other_sections_without_overwriting_them():
    raw = profile_options(feature={"vehicle_soc_entity": "sensor.first"})
    draft = OptionsDraft(
        "vehicle", raw, {"vehicle_soc_entity", "timed_goal_reset_pending"}
    )
    draft.values["vehicle_soc_entity"] = "sensor.replacement"
    draft.values["timed_goal_reset_pending"] = True
    current = deepcopy(raw)
    current["charging_price"] = 0.6
    current["vehicle_profiles"][A]["name"] = "Renamed"
    current["vehicle_profiles"][A]["options"]["timed_capacity_kwh"] = 80
    result = draft.merge(current)
    assert result["charging_price"] == 0.6
    assert result["vehicle_profiles"][A]["name"] == "Renamed"
    assert result["vehicle_profiles"][A]["options"] == {
        "vehicle_soc_entity": "sensor.replacement",
        "timed_capacity_kwh": 80,
    }
    assert result["vehicle_profiles"][B] == raw["vehicle_profiles"][B]
    assert result["timed_goal_reset_pending"] is True
    assert "vehicle_soc_entity" not in result


@pytest.mark.parametrize(
    "change", ["selection", "device", "section", "roundtrip", "removed", "deactivated"]
)
def test_stale_profile_draft_cannot_write_into_another_vehicle(change):
    raw = profile_options(feature={"vehicle_soc_entity": "sensor.first"})
    draft = OptionsDraft("vehicle", raw, {"vehicle_soc_entity"})
    current = deepcopy(raw)
    if change == "selection":
        current["active_vehicle_profile"] = B
    elif change == "device":
        current["vehicle_profiles"][A]["device_id"] = "another-car"
    elif change == "section":
        current["vehicle_profiles"][A]["options"]["vehicle_soc_entity"] = (
            "sensor.changed"
        )
    elif change == "roundtrip":
        current["vehicle_profile_selected_at"] = 1234
    elif change == "removed":
        current["vehicle_profiles"].pop(A)
    else:
        current["active_vehicle_profile"] = None
    with pytest.raises(OptionsChangedError):
        draft.merge(current)


def test_profile_draft_deletes_only_selected_feature_and_keeps_global_reset_global():
    raw = profile_options(
        feature={"vehicle_soc_entity": "sensor.first", "timed_capacity_kwh": 80}
    )
    raw["timed_goal_reset_pending"] = True
    draft = OptionsDraft(
        "vehicle", raw, {"vehicle_soc_entity", "timed_goal_reset_pending"}
    )
    draft.clear({"vehicle_soc_entity", "timed_goal_reset_pending"})
    result = draft.merge(raw)
    assert result["vehicle_profiles"][A]["options"] == {"timed_capacity_kwh": 80}
    assert "timed_goal_reset_pending" not in result
    empty = profile_options(active=None)
    with pytest.raises(OptionsChangedError):
        OptionsDraft("vehicle", empty, {"vehicle_soc_entity"}).merge(empty)
