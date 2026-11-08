"""Profile setup offers usable local providers without losing saved bindings."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.helpers import device_registry as dr

from tests.ha_fakes import FakeConfigEntry
from tests.test_options_flow import fields
from tests.test_vehicle_profile_options import Flow
from tests.test_vehicle_profiles import A, profile_options


def provider_choices(form):
    return fields(form)["vehicle_provider"].config["options"]


@pytest.mark.parametrize("kind", ["missing", "account", "ambiguous", "vehicle"])
def test_new_profile_offers_only_a_provider_with_a_real_vehicle(kind):
    async def run():
        flow = Flow(FakeConfigEntry())
        registry = flow.hass.data[dr.DATA_REGISTRY]
        if kind == "missing":
            registry.devices = {}
            registry.async_get = Mock(return_value=None)
        elif kind in {"account", "ambiguous"}:
            identifiers = {("bavariandata", "account-entry")}
            if kind == "ambiguous":
                identifiers = {
                    ("bavariandata", "WBA12345678901234"),
                    ("bavariandata", "WBA12345678901235"),
                }
            device = SimpleNamespace(
                config_entry_id="account-entry", identifiers=identifiers,
            )
            registry.devices = {"car": device}
            registry.async_get = Mock(return_value=device)
        await flow.async_step_vehicle_profiles()
        form = await flow.async_step_vehicle_profile_add()
        assert provider_choices(form) == (
            ["none", "bavariandata"] if kind == "vehicle" else ["none"]
        )
        assert form["errors"] == {}
        assert flow.config_entry.options == {}

    asyncio.run(run())


def test_unavailable_provider_cannot_be_submitted_for_a_new_profile():
    async def run():
        flow = Flow(FakeConfigEntry())
        registry = flow.hass.data[dr.DATA_REGISTRY]
        registry.devices = {}
        registry.async_get = Mock(return_value=None)
        await flow.async_step_vehicle_profiles()
        result = await flow.async_step_vehicle_profile_add({
            "vehicle_profile_name": "BMW", "vehicle_provider": "bavariandata",
        })
        assert result["step_id"] == "vehicle_profile_add"
        assert result["errors"] == {"vehicle_provider": "vehicle_provider_invalid"}
        assert provider_choices(result) == ["none"]
        assert flow.config_entry.options == {}

    asyncio.run(run())


def test_missing_saved_provider_can_be_renamed_without_losing_binding_or_features():
    async def run():
        raw = profile_options()
        raw["vehicle_profiles"][A]["identity"] = "saved-identity"
        raw["vehicle_profiles"][A]["options"] = {"vehicle_soc_use_profile": True}
        flow = Flow(FakeConfigEntry(options=raw))
        registry = flow.hass.data[dr.DATA_REGISTRY]
        registry.devices = {}
        registry.async_get = Mock(return_value=None)
        await flow.async_step_vehicle_profiles()
        form = await flow.async_step_vehicle_profile_edit_select({"vehicle_profile_id": A})
        assert provider_choices(form) == ["none", "bavariandata"]
        assert form["errors"] == {}
        result = await flow.async_step_vehicle_profile_edit({
            "vehicle_profile_name": "New name", "vehicle_provider": "bavariandata",
        })
        expected = deepcopy(raw)
        expected["vehicle_profiles"][A]["name"] = "New name"
        assert result["type"] == "create_entry"
        assert result["data"] == expected
        assert flow.config_entry.options == raw

    asyncio.run(run())


def test_missing_saved_provider_can_be_replaced_with_manual_sources_explicitly():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options()))
        flow.hass.data[dr.DATA_REGISTRY].devices = {}
        await flow.async_step_vehicle_profiles()
        await flow.async_step_vehicle_profile_edit_select({"vehicle_profile_id": A})
        result = await flow.async_step_vehicle_profile_edit({
            "vehicle_profile_name": "Manual car", "vehicle_provider": "none",
        })
        profile = result["data"]["vehicle_profiles"][A]
        assert profile["provider"] == "none"
        assert profile["device_id"] == ""
        assert profile["options"] == {}

    asyncio.run(run())
