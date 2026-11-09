"""Optional loss settings save independently and validate their selected source."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.helpers import device_registry as dr

from tests.ha_fakes import FakeConfigEntry
from tests.test_bavarian_history import vehicle_registry
from tests.test_options_flow import Flow as BaseFlow
from tests.test_options_flow import fields


class Flow(BaseFlow):
    def __init__(self, entry):
        super().__init__(entry)
        registry = vehicle_registry()
        device = registry.async_get.return_value
        registry.async_get.side_effect = lambda key: (
            device if key in {"car", "own"} else None
        )
        # The registered car belongs to one BavarianData account entry.
        registry._devices = SimpleNamespace(
            get_devices_for_config_entry_id=lambda entry_id: (
                [SimpleNamespace(id="car")] if entry_id == "account-entry" else []
            )
        )
        self.hass = SimpleNamespace(
            data={dr.DATA_REGISTRY: registry}, config=SimpleNamespace(country="DE"),
            config_entries=SimpleNamespace(async_entries=lambda domain: (
                [SimpleNamespace(entry_id="account-entry")] if domain == "bavariandata" else []
            )),
        )


@pytest.mark.parametrize("source", ["sensor", "vehicle"])
def test_loss_source_is_independent_and_atomic(source):
    async def run():
        entry = FakeConfigEntry(
            options={
                "charging_price": 0.3,
                "scan_interval_seconds": 40,
                "charge_loss_bavarian_device": "car",
                "charge_loss_auto_match": True,
            }
        )
        flow = Flow(entry)
        menu = await flow.async_step_losses()
        assert menu["menu_options"] == [
            "loss_sensor",
            "loss_vehicle",
            "vehicle_profile",
        ]
        form = await getattr(flow, f"async_step_loss_{source}")()
        if source == "sensor":
            assert fields(form)["vehicle_last_charge_energy_entity"].config[
                "filter"
            ] == [{"domain": ["sensor"]}]
            result = await flow.async_step_loss_sensor(
                {"vehicle_last_charge_energy_entity": "sensor.energy"}
            )
        else:
            assert fields(form)["charge_loss_vehicle_provider"].config["options"] == [
                "bavariandata"
            ]
            details = await flow.async_step_loss_vehicle(
                {"charge_loss_vehicle_provider": "bavariandata"}
            )
            assert (
                fields(details)["charge_loss_vehicle_device"].config["integration"]
                == "bavariandata"
            )
            result = await flow.async_step_loss_vehicle_details(
                {"charge_loss_vehicle_device": "car"}
            )
        assert result["type"] == "create_entry"
        assert "charge_loss_source" not in entry.options
        saved = result["data"]
        assert saved["charging_price"] == 0.3
        assert saved["scan_interval_seconds"] == 40
        assert saved["charge_loss_source"] == (
            "sensor" if source == "sensor" else "vehicle_history"
        )
        assert "charge_loss_bavarian_device" not in saved
        if source == "sensor":
            assert saved["charge_loss_auto_match"] is False
        entry.options = saved
        assert "loss_disable" in (await flow.async_step_losses())["menu_options"]
        cleared = (await flow.async_step_loss_disable())["data"]
        assert cleared == {"charging_price": 0.3, "scan_interval_seconds": 40}
        entry.options = cleared
        assert "loss_disable" not in (await flow.async_step_losses())["menu_options"]

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [None, True, "", "bad", "switch.energy"])
def test_loss_sensor_validation_and_correction(invalid):
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_losses()
        assert (
            await flow.async_step_loss_sensor(
                {"vehicle_last_charge_energy_entity": invalid}
            )
        )["errors"]
        assert (
            await flow.async_step_loss_sensor(
                {
                    "vehicle_last_charge_energy_entity": "sensor.energy",
                    "charge_loss_auto_match": "yes",
                }
            )
        )["errors"]
        assert (
            await flow.async_step_loss_sensor(
                {
                    "vehicle_last_charge_energy_entity": "sensor.energy",
                    "charge_loss_auto_match": True,
                }
            )
        )["type"] == "create_entry"

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [None, True, "unknown"])
def test_loss_provider_and_device_validation(invalid):
    async def run():
        flow = Flow(FakeConfigEntry())
        await flow.async_step_losses()
        assert (
            await flow.async_step_loss_vehicle(
                {"charge_loss_vehicle_provider": invalid}
            )
        )["errors"]
        await flow.async_step_loss_vehicle(
            {"charge_loss_vehicle_provider": "bavariandata"}
        )
        assert (
            await flow.async_step_loss_vehicle_details(
                {"charge_loss_vehicle_device": invalid}
            )
        )["errors"]
        assert (
            await flow.async_step_loss_vehicle_details(
                {"charge_loss_vehicle_device": "car"}
            )
        )["type"] == "create_entry"

    asyncio.run(run())


@pytest.mark.parametrize(
    "options",
    [
        {"timed_vehicle_provider": "bavariandata", "timed_vehicle_device": "car"},
        {"charge_loss_bavarian_device": "own", "timed_bavarian_device": "other"},
    ],
)
def test_loss_reuses_other_source_only_when_own_selection_absent(options):
    async def run():
        flow = Flow(FakeConfigEntry(options=options))
        await flow.async_step_losses()
        form = await flow.async_step_loss_vehicle(
            {"charge_loss_vehicle_provider": "bavariandata"}
        )
        marker = next(iter(form["data_schema"].schema))
        assert marker.description["suggested_value"] == options.get(
            "charge_loss_bavarian_device", "car"
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "step", ["loss_sensor", "loss_vehicle", "loss_vehicle_details", "loss_disable"]
)
def test_loss_steps_cannot_skip_section(step):
    async def run():
        assert (await getattr(Flow(FakeConfigEntry()), f"async_step_{step}")())[
            "step_id"
        ] == "init"

    asyncio.run(run())


@pytest.mark.parametrize("source", [None, "disabled"])
def test_unconfigured_loss_estimates_do_not_offer_a_noop_disable_action(source):
    async def run():
        options = {} if source is None else {"charge_loss_source": source}
        flow = Flow(FakeConfigEntry(options=options))
        menu = await flow.async_step_losses()
        assert "loss_disable" not in menu["menu_options"]
        assert menu["description_placeholders"] == {"vehicle": "—"}

    asyncio.run(run())


def test_loss_forms_identify_the_profile_bound_to_the_draft():
    from tests.test_vehicle_profiles import A, B, profile_options

    async def run():
        entry = FakeConfigEntry(options=profile_options(provider="none"))
        flow = Flow(entry)
        flow._profile_target = A
        menu = await flow.async_step_losses()
        expected = {"vehicle": entry.options["vehicle_profiles"][A]["name"]}
        assert menu["description_placeholders"] == expected
        form = await flow.async_step_loss_sensor()
        assert form["description_placeholders"] == expected
        # A concurrent vehicle change cannot relabel the old profile's draft.
        entry.options = {**entry.options, "active_vehicle_profile": B}
        form = await flow.async_step_loss_sensor()
        assert form["description_placeholders"] == expected
        rejected = await flow.async_step_loss_sensor(
            {"vehicle_last_charge_energy_entity": "sensor.battery_energy"}
        )
        assert rejected["step_id"] == "options_changed"

    asyncio.run(run())


def test_missing_profile_history_has_a_working_manual_and_profile_setup_route():
    from tests.test_vehicle_profiles import A, profile_options

    async def run():
        options = profile_options()
        options["vehicle_profiles"][A]["identity"] = "previous-vehicle"
        entry = FakeConfigEntry(options=options)
        flow = Flow(entry)
        flow._profile_target = A
        menu = await flow.async_step_losses()
        assert menu["menu_options"] == ["loss_sensor", "vehicle_profile"]
        # A stale link or a vehicle disappearing after the menu was opened
        # returns useful choices instead of a repeatedly failing empty form.
        fallback = await flow.async_step_loss_profile()
        assert fallback["type"] == "menu"
        assert fallback["menu_options"] == menu["menu_options"]
        form = await flow.async_step_loss_sensor()
        assert "vehicle_last_charge_energy_entity" in fields(form)
        assert entry.options == options

    asyncio.run(run())


def test_losses_need_a_chosen_profile_once_profiles_exist():
    from tests.test_vehicle_profiles import B, profile_options

    async def run():
        entry = FakeConfigEntry(options=profile_options(provider="none"))
        flow = Flow(entry)
        assert (await flow.async_step_losses())["step_id"] == "vehicle"
        # A car that is not selected keeps its own loss source.
        flow._profile_target = B
        await flow.async_step_losses()
        result = await flow.async_step_loss_sensor(
            {"vehicle_last_charge_energy_entity": "sensor.second_energy"}
        )
        options = result["data"]["vehicle_profiles"][B]["options"]
        assert options["vehicle_last_charge_energy_entity"] == "sensor.second_energy"
        assert result["data"]["active_vehicle_profile"] != B

    asyncio.run(run())
