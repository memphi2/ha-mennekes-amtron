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
        self.hass = SimpleNamespace(
            data={dr.DATA_REGISTRY: registry}, config=SimpleNamespace(country="DE")
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
            "loss_disable",
            "back",
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
        assert result["step_id"] == "review"
        assert "charge_loss_source" not in entry.options
        saved = (await flow.async_step_save())["data"]
        assert saved["charging_price"] == 0.3
        assert saved["scan_interval_seconds"] == 40
        assert saved["charge_loss_source"] == (
            "sensor" if source == "sensor" else "vehicle_history"
        )
        assert "charge_loss_bavarian_device" not in saved
        if source == "sensor":
            assert saved["charge_loss_auto_match"] is False
        entry.options = saved
        await flow.async_step_losses()
        await flow.async_step_loss_disable()
        cleared = (await flow.async_step_save())["data"]
        assert cleared == {"charging_price": 0.3, "scan_interval_seconds": 40}

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
        )["step_id"] == "review"

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
        )["step_id"] == "review"

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
