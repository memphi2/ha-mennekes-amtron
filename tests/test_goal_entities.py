"""Energy-goal UI and the optional service contract retain their semantics."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.energy_goal_controller import (
    EnergyGoalController,
)
from custom_components.mennekes_amtron_rtu.number import (
    async_setup_entry as setup_numbers,
)
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from tests.ha_fakes import build_runtime, collect
from tests.test_modbus_service import TOKEN, service_fixture


def test_goal_entities_never_start_charging_or_override_pause():
    async def run():
        hass, entry, transport = await build_runtime()
        control = entry.runtime_data.control
        control.pause_requested = True
        saved = AsyncMock()
        runner = EnergyGoalController(control, entry.runtime_data.client, saved)
        numbers, sensors = {}, {}
        await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        target, status = numbers["energy_goal"], sensors["energy_goal_status"]
        assert target.available
        assert status.available
        assert target.native_value == 0
        assert status.native_value == "disabled"
        await target.async_set_native_value(12)
        assert target.native_value == 12
        assert status.native_value == "active"
        assert control.pause_requested is True
        assert transport.writes == []
        assert saved.await_count == 1
        await target.async_set_native_value(0)
        assert status.native_value == "disabled"
        assert control.pause_requested is True
        assert transport.writes == []
        await runner.async_stop()

    asyncio.run(run())


def test_old_layouts_do_not_get_an_unusable_goal():
    async def run():
        hass, entry, _ = await build_runtime(layout=R.LAYOUT_V01_00)
        numbers, sensors = {}, {}
        await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        assert "energy_goal" not in numbers
        assert "energy_goal_status" not in sensors

    asyncio.run(run())


def test_remote_goal_and_quality_metadata_are_optional_and_change_snapshots(tmp_path):
    async def run():
        service, _, _ = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = RemoteClient(session, "http://localhost", TOKEN)
            first = remote.apply_snapshot(service.snapshot())
            wrapper = RemoteControl(
                remote, diagnostics=service.diagnostics, options=service.control.options,
                state=lambda: service.data, request_refresh=AsyncMock(),
            )
            remote.execute = AsyncMock()
            assert wrapper.supports_energy_goal
            await wrapper.async_set_energy_goal(12)
            remote.execute.assert_awaited_once_with("energy_goal", value=12)
            with pytest.raises(ServiceValidationError):
                await wrapper.async_set_energy_goal(float("nan"))
            changed = deepcopy(service.snapshot())
            changed["control"]["energy_goal"] = {
                "target_kwh": 12.0, "baseline_kwh": 1200.0, "status": "active",
            }
            changed["connection_quality"] = "degraded"
            second = remote.apply_snapshot(changed)
            assert second != first
            assert second.connection_quality == "degraded"
            assert wrapper.energy_goal.target_kwh == 12
            legacy = deepcopy(service.snapshot())
            legacy["control"].pop("supports_energy_goal")
            legacy["control"].pop("energy_goal")
            legacy.pop("connection_quality")
            remote.apply_snapshot(legacy)
            assert not wrapper.supports_energy_goal
            with pytest.raises(ServiceValidationError):
                await wrapper.async_set_energy_goal(12)
        await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.update(connection_quality="private arbitrary value"),
    lambda raw: raw.update(connection_quality=[]),
    lambda raw: raw["control"].update(supports_energy_goal=1),
    lambda raw: raw["control"].update(energy_goal=None),
    lambda raw: raw["control"].update(energy_goal={"target_kwh": True}),
])
def test_bad_optional_goal_metadata_is_rejected_without_commands(tmp_path, mutate):
    async def run():
        service, transport, _ = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = RemoteClient(session, "http://localhost", TOKEN)
            raw = deepcopy(service.snapshot())
            mutate(raw)
            before = list(transport.writes)
            with pytest.raises(ServiceConnectionError, match="invalid service snapshot"):
                remote.apply_snapshot(raw)
            assert not remote.connected
            assert transport.writes == before
        await service.stop()

    asyncio.run(run())
