"""Virtual charging permission is explicit, scoped and independent of selection."""

import asyncio
from copy import deepcopy

import pytest

from custom_components.mennekes_amtron_rtu.vehicle_profiles import (
    CONF_VEHICLE_PLUG_CHARGE,
    CONF_VIRTUAL_PLUG_CHARGE,
    profiles,
    vehicle_options,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources import provider_identity
from tests.ha_fakes import FakeConfigEntry
from tests.test_options_flow import fields
from tests.test_vehicle_detection_options import detected_options, select_detector
from tests.test_vehicle_profile_options import Flow
from tests.test_vehicle_profiles import A, B, profile_options


def allowed_options():
    raw = detected_options()
    raw[CONF_VIRTUAL_PLUG_CHARGE] = True
    raw["vehicle_profiles"][A]["options"][CONF_VEHICLE_PLUG_CHARGE] = True
    return raw


@pytest.mark.parametrize("raw", [{}, profile_options(), profile_options(active=None)])
def test_gate_is_available_without_permission_or_active_profile(raw):
    async def run():
        entry = FakeConfigEntry(options=raw)
        flow = Flow(entry)
        assert (
            "vehicle_plug_charge" in (await flow.async_step_vehicle())["menu_options"]
        )
        form = await flow.async_step_vehicle_plug_charge()
        assert set(fields(form)) == {CONF_VIRTUAL_PLUG_CHARGE}
        assert form["description_placeholders"]["vehicle"] == (
            "Car A" if raw.get("active_vehicle_profile") == A else "—"
        )
        saved = (
            await flow.async_step_vehicle_plug_charge({CONF_VIRTUAL_PLUG_CHARGE: True})
        )["data"]
        assert saved == {**raw, CONF_VIRTUAL_PLUG_CHARGE: True}
        assert entry.options == raw

    asyncio.run(run())


@pytest.mark.parametrize("automatic", [False, True])
def test_gate_and_active_permission_save_without_selecting_a_car_or_enabling_other_features(
    automatic,
):
    async def run():
        raw = detected_options()
        raw["vehicle_auto_selection"] = automatic
        flow = Flow(FakeConfigEntry(options=raw))
        form = await flow.async_step_vehicle_plug_charge()
        assert set(fields(form)) == {CONF_VIRTUAL_PLUG_CHARGE, CONF_VEHICLE_PLUG_CHARGE}
        saved = (
            await flow.async_step_vehicle_plug_charge(
                {CONF_VIRTUAL_PLUG_CHARGE: True, CONF_VEHICLE_PLUG_CHARGE: True}
            )
        )["data"]
        assert saved[CONF_VIRTUAL_PLUG_CHARGE] is True
        assert profiles(saved)[A].options == {CONF_VEHICLE_PLUG_CHARGE: True}
        assert profiles(saved)[B] == profiles(raw)[B]
        assert saved["active_vehicle_profile"] == A
        assert saved["vehicle_auto_selection"] is automatic
        assert "timed_goal_reset_pending" not in saved
        assert "vehicle_profile_selected_at" not in saved
        assert CONF_VEHICLE_PLUG_CHARGE not in raw["vehicle_profiles"][A]["options"]

    asyncio.run(run())


@pytest.mark.parametrize("key", [CONF_VIRTUAL_PLUG_CHARGE, CONF_VEHICLE_PLUG_CHARGE])
@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_both_switches_accept_only_actual_booleans(key, value):
    async def run():
        raw = detected_options()
        flow = Flow(FakeConfigEntry(options=raw))
        form = await flow.async_step_vehicle_plug_charge(
            {CONF_VIRTUAL_PLUG_CHARGE: True, CONF_VEHICLE_PLUG_CHARGE: True, key: value}
        )
        assert form["errors"][key] == "plug_charge_invalid"
        assert flow.config_entry.options == raw

    asyncio.run(run())


@pytest.mark.parametrize("raw", [{}, profile_options(), profile_options(active=None)])
def test_permission_cannot_be_enabled_without_an_active_profile_and_evidence(raw):
    async def run():
        flow = Flow(FakeConfigEntry(options=raw))
        result = await flow.async_step_vehicle_plug_charge(
            {CONF_VIRTUAL_PLUG_CHARGE: True, CONF_VEHICLE_PLUG_CHARGE: True}
        )
        assert (
            result["errors"][CONF_VEHICLE_PLUG_CHARGE]
            == "plug_charge_detection_required"
        )
        assert flow.config_entry.options == raw

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["active", "detection", "catalog"])
def test_gate_can_always_be_disabled_even_after_profile_or_source_disappears(missing):
    async def run():
        raw = allowed_options()
        if missing == "active":
            raw["active_vehicle_profile"] = None
        elif missing == "detection":
            raw["vehicle_profiles"][A].pop("detection")
        else:
            raw["vehicle_profiles"] = {}
        flow = Flow(FakeConfigEntry(options=raw))
        assert set(fields(await flow.async_step_vehicle_plug_charge())) == {
            CONF_VIRTUAL_PLUG_CHARGE
        }
        saved = (
            await flow.async_step_vehicle_plug_charge({CONF_VIRTUAL_PLUG_CHARGE: False})
        )["data"]
        assert saved[CONF_VIRTUAL_PLUG_CHARGE] is False
        if missing == "detection":
            assert CONF_VEHICLE_PLUG_CHARGE not in profiles(saved)[A].options
        elif missing == "active":
            assert profiles(saved)[A].options[CONF_VEHICLE_PLUG_CHARGE] is True

    asyncio.run(run())


def test_global_disable_and_profile_revoke_are_separate_actions():
    async def run():
        entry = FakeConfigEntry(options=allowed_options())
        flow = Flow(entry)
        saved = (
            await flow.async_step_vehicle_plug_charge({CONF_VIRTUAL_PLUG_CHARGE: False})
        )["data"]
        assert profiles(saved)[A].options[CONF_VEHICLE_PLUG_CHARGE] is True
        entry.options = saved
        revoked = (
            await flow.async_step_vehicle_plug_charge(
                {CONF_VIRTUAL_PLUG_CHARGE: True, CONF_VEHICLE_PLUG_CHARGE: False}
            )
        )["data"]
        assert revoked[CONF_VIRTUAL_PLUG_CHARGE] is True
        assert CONF_VEHICLE_PLUG_CHARGE not in profiles(revoked)[A].options

    asyncio.run(run())


@pytest.mark.parametrize(
    "changed",
    ["gate", "identity", "source", "permission", "selection", "roundtrip", "removed"],
)
def test_open_permission_form_cannot_overwrite_newer_configuration(changed):
    async def run():
        entry = FakeConfigEntry(options=detected_options())
        flow = Flow(entry)
        await flow.async_step_vehicle_plug_charge()
        if changed == "gate":
            entry.options[CONF_VIRTUAL_PLUG_CHARGE] = False
        elif changed == "selection":
            entry.options["active_vehicle_profile"] = B
        elif changed == "roundtrip":
            entry.options["vehicle_profile_selected_at"] = 1234
        elif changed == "removed":
            entry.options["vehicle_profiles"].pop(A)
        elif changed == "identity":
            entry.options["vehicle_profiles"][A]["device_id"] = "another-car"
        elif changed == "source":
            entry.options["vehicle_profiles"][A]["detection"]["connected_entity"] = (
                "binary_sensor.another_car"
            )
        else:
            entry.options["vehicle_profiles"][A]["options"][
                CONF_VEHICLE_PLUG_CHARGE
            ] = False
        live = deepcopy(entry.options)
        result = await flow.async_step_vehicle_plug_charge(
            {CONF_VIRTUAL_PLUG_CHARGE: True, CONF_VEHICLE_PLUG_CHARGE: True}
        )
        assert result["step_id"] == "options_changed"
        assert entry.options == live

    asyncio.run(run())


def test_unrelated_price_or_automatic_selection_edits_are_preserved():
    async def run():
        entry = FakeConfigEntry(options=detected_options())
        flow = Flow(entry)
        await flow.async_step_vehicle_plug_charge()
        entry.options.update(charging_price=0.6, vehicle_auto_selection=True)
        saved = (
            await flow.async_step_vehicle_plug_charge(
                {CONF_VIRTUAL_PLUG_CHARGE: True, CONF_VEHICLE_PLUG_CHARGE: True}
            )
        )["data"]
        assert saved["charging_price"] == 0.6
        assert saved["vehicle_auto_selection"] is True

    asyncio.run(run())


@pytest.mark.parametrize("change", ["same", "source", "remove"])
def test_only_changed_detection_sources_invalidate_the_permission(change):
    async def run():
        raw = allowed_options()
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
        assert profiles(saved)[A].options.get(CONF_VEHICLE_PLUG_CHARGE, False) is (
            change == "same"
        )
        assert saved[CONF_VIRTUAL_PLUG_CHARGE] is True

    asyncio.run(run())


def test_renaming_keeps_permission_but_changing_vehicle_identity_removes_it():
    async def run():
        entry = FakeConfigEntry(options=allowed_options())
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
        assert profiles(saved)[A].options[CONF_VEHICLE_PLUG_CHARGE] is True
        entry.options = saved
        await flow.async_step_vehicle_profiles()
        await flow.async_step_vehicle_profile_edit_select({"vehicle_profile_id": A})
        changed = (
            await flow.async_step_vehicle_profile_edit(
                {"vehicle_profile_name": "Other vehicle", "vehicle_provider": "none"}
            )
        )["data"]
        assert CONF_VEHICLE_PLUG_CHARGE not in profiles(changed)[A].options
        assert changed[CONF_VIRTUAL_PLUG_CHARGE] is True

    asyncio.run(run())


def test_manual_selection_and_soc_disable_cannot_grant_or_remove_virtual_permissions():
    async def run():
        entry = FakeConfigEntry(options=allowed_options())
        entry.options["vehicle_profiles"][A]["options"]["vehicle_soc_entity"] = (
            "sensor.car"
        )
        flow = Flow(entry)
        await flow.async_step_vehicle()
        saved = (await flow.async_step_vehicle_disable())["data"]
        assert saved[CONF_VIRTUAL_PLUG_CHARGE] is True
        assert profiles(saved)[A].options[CONF_VEHICLE_PLUG_CHARGE] is True
        entry.options = saved
        await flow.async_step_vehicle_profiles()
        selected = (
            await flow.async_step_vehicle_profile_select({"vehicle_profile_id": B})
        )["data"]
        assert selected[CONF_VIRTUAL_PLUG_CHARGE] is True
        assert CONF_VEHICLE_PLUG_CHARGE not in vehicle_options(selected)
        assert profiles(selected)[A].options[CONF_VEHICLE_PLUG_CHARGE] is True

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_malformed_persisted_permission_cannot_authorize_any_profile(value):
    raw = allowed_options()
    raw["vehicle_profiles"][A]["options"][CONF_VEHICLE_PLUG_CHARGE] = value
    assert profiles(raw) == {}
    assert CONF_VEHICLE_PLUG_CHARGE not in vehicle_options(raw)
