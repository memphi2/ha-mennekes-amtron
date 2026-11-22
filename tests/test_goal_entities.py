"""Energy-goal UI and the optional service contract retain their semantics."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.energy_goal import CostPart
from custom_components.mennekes_amtron_rtu.number import (
    async_setup_entry as setup_numbers,
)
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from tests.ha_fakes import build_runtime, collect
from tests.test_modbus_service import TOKEN, service_fixture


def test_goal_entities_never_start_charging_or_override_pause():
    async def run():
        hass, entry, transport = await build_runtime()
        control = entry.runtime_data.control
        await control.async_pause_charging()
        await entry.runtime_data.coordinator.async_refresh()
        assert control.pause_requested is True
        transport.writes.clear()
        numbers, sensors = {}, {}
        await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        target, waiting = numbers["energy_goal"], sensors["operation_waiting"]

        def status():
            return waiting.extra_state_attributes["energy_target"]

        assert target.available
        assert waiting.available
        assert target.native_value == 0
        assert status() == "disabled"
        progress = sensors["target_progress"]
        assert progress.native_value is None
        assert progress.extra_state_attributes is None
        await target.async_set_native_value(12)
        assert target.native_value == 12
        assert status() == "active"
        assert progress.native_value == 0
        assert progress.extra_state_attributes == {"target": "energy", "target_value": 12}
        goal = entry.runtime_data.control.energy_goal
        # Rounded down: 98.7 % is 98, never a premature 99 or 100.
        goal.progress = lambda _total: 0.987
        assert progress.native_value == 98
        del goal.progress
        entry.runtime_data.control.energy_goal.cost = CostPart(5.0, 0.0, 0.3, 0.0)
        assert progress.extra_state_attributes == {"target": "cost", "target_value": 5.0}
        assert control.pause_requested is True
        assert transport.writes == []
        # The service keeps the goal across its own restart.
        saved = json.loads(entry.service.state_path.read_text())
        assert saved["energy_goal"]["target_kwh"] == 12
        await target.async_set_native_value(0)
        assert status() == "disabled"
        assert control.pause_requested is True
        assert transport.writes == []
        assert json.loads(entry.service.state_path.read_text())["energy_goal"]["status"] == "disabled"

    asyncio.run(run())


def test_old_layouts_do_not_get_an_unusable_goal():
    async def run():
        hass, entry, _ = await build_runtime(layout=R.LAYOUT_V01_00)
        numbers, sensors = {}, {}
        await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        assert "energy_goal" not in numbers
        assert "energy_target" not in sensors["operation_waiting"].extra_state_attributes
        assert "target_progress" not in sensors

    asyncio.run(run())


def test_remote_capabilities_and_quality_change_snapshots_but_old_formats_are_rejected(tmp_path):
    async def run():
        service, _, _ = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = ServiceClient(session, "http://localhost", TOKEN)
            first = remote.apply_snapshot(service.snapshot())
            wrapper = ServiceControl(
                remote,                 state=lambda: service.data, request_refresh=AsyncMock(),
            )
            remote.execute = AsyncMock()
            assert wrapper.supports_energy_goal
            await wrapper.async_set_energy_goal(12)
            remote.execute.assert_awaited_once_with("energy_goal", value=12)
            await wrapper.async_set_cost_goal(10, 0.30, "EUR")
            remote.execute.assert_awaited_with("cost_goal", value=10, price=0.30, currency="EUR")
            await wrapper.async_set_cost_goal(0, None, "EUR")
            remote.execute.assert_awaited_with("cost_goal", value=0, price=None, currency="EUR")
            await wrapper.async_set_cost_price(0.5, "CHF")
            remote.execute.assert_awaited_with("cost_price", price=0.5, currency="CHF")
            # Only Home Assistant knows that it has no price; the service
            # judges every value.
            remote.execute.reset_mock()
            with pytest.raises(ServiceValidationError):
                await wrapper.async_set_cost_goal(10, None, "EUR")
            remote.execute.assert_not_awaited()
            changed = deepcopy(service.snapshot())
            changed["control"]["energy_goal"] = {
                "target_kwh": 12.0, "baseline_kwh": 1200.0, "status": "active",
            }
            changed["connection_quality"] = "degraded"
            changed["connection_failures"] = 7
            second = remote.apply_snapshot(changed)
            assert second != first
            assert second.connection_quality == "degraded"
            assert second.connection_failures == 7
            assert wrapper.energy_goal.target_kwh == 12
            legacy = deepcopy(service.snapshot())
            legacy["control"].pop("supports_energy_goal")
            legacy["control"].pop("energy_goal")
            legacy.pop("connection_quality")
            legacy.pop("connection_failures")
            with pytest.raises(ServiceConnectionError, match="connection_quality"):
                remote.apply_snapshot(legacy)
            unsupported = deepcopy(service.snapshot())
            unsupported["control"]["supports_energy_goal"] = False
            remote.apply_snapshot(unsupported)
            assert not wrapper.supports_energy_goal
        await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.update(connection_quality="private arbitrary value"),
    lambda raw: raw.update(connection_quality=[]),
    lambda raw: raw.update(connection_failures=-1),
    lambda raw: raw.update(connection_failures=True),
    lambda raw: raw.update(connection_failures="3"),
    lambda raw: raw["control"].update(supports_energy_goal=1),
    lambda raw: raw["control"].update(energy_goal=None),
    lambda raw: raw["control"].update(energy_goal={"target_kwh": True}),
])
def test_bad_optional_goal_metadata_is_rejected_without_commands(tmp_path, mutate):
    async def run():
        service, transport, _ = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = ServiceClient(session, "http://localhost", TOKEN)
            raw = deepcopy(service.snapshot())
            mutate(raw)
            before = list(transport.writes)
            with pytest.raises(ServiceConnectionError, match="invalid service snapshot"):
                remote.apply_snapshot(raw)
            assert not remote.connected
            assert transport.writes == before
        await service.stop()

    asyncio.run(run())
