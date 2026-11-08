"""Optional vehicle targets retain HA entity and remote service boundaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.number import (
    async_setup_entry as setup_numbers,
)
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.sensor import (
    async_setup_entry as setup_sensors,
)
from custom_components.mennekes_amtron_rtu.vehicle_soc_const import (
    CONF_VEHICLE_SOC_ENTITY,
)
from tests.ha_fakes import build_runtime, collect
from tests.test_modbus_service import TOKEN, current_writes, service_fixture

SOURCE = "sensor.car_soc|number.car_limit"


def test_vehicle_entities_are_absent_until_a_source_is_selected():
    async def run():
        hass, entry, _transport = await build_runtime()
        numbers, sensors = {}, {}
        await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        assert "soc_goal" not in numbers
        assert "soc_goal_status" not in sensors
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("read_only", [False, True])
def test_vehicle_number_uses_reported_cap_and_respects_write_permission(read_only):
    async def run():
        hass, entry, transport = await build_runtime(read_only=read_only)
        entry.options[CONF_VEHICLE_SOC_ENTITY] = "sensor.car_soc"
        observer = SimpleNamespace(async_set_target=AsyncMock())
        entry.runtime_data.vehicle_soc = observer
        control = entry.runtime_data.control
        control.soc_goal.configure_source(SOURCE)
        numbers, sensors = {}, {}
        await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
        await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
        target, status = numbers["soc_goal"], sensors["soc_goal_status"]
        assert target.available is not read_only
        assert status.available
        assert target.native_unit_of_measurement == "%"
        assert target.native_max_value == 100
        control.soc_goal.observe(20, 80)
        assert target.native_max_value == 80
        assert target.native_value == 0
        if not read_only:
            await target.async_set_native_value(75)
            observer.async_set_target.assert_awaited_once_with(75)
        assert transport.writes == []
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_remote_vehicle_observations_need_no_extra_wallbox_poll_and_old_service_is_unavailable(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                wrapper = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                assert wrapper.supports_soc_goal
                before_writes = current_writes(transport)
                await wrapper.async_configure_soc(SOURCE)
                await wrapper.async_set_soc_goal(80, source=SOURCE, soc=20, maximum=90)
                assert wrapper.soc_goal.target_percent == 80
                before_reads = list(transport.reads)
                await wrapper.async_observe_soc(None, source=SOURCE)
                assert wrapper.soc_goal.status == "active"
                await wrapper.async_observe_soc(30, 70, source=SOURCE)
                assert wrapper.soc_goal.target_percent == 70
                # Clamped to the car's limit, the target now follows that limit.
                await wrapper.async_observe_soc(40, 90, source=SOURCE)
                assert wrapper.soc_goal.target_percent == 90
                assert wrapper.soc_goal.maximum_percent == 90
                assert current_writes(transport) == before_writes
                assert transport.reads == before_reads

                hass, entry, _ = await build_runtime()
                entry.options[CONF_VEHICLE_SOC_ENTITY] = "sensor.car_soc"
                direct_client = entry.runtime_data.client
                entry.runtime_data.control = wrapper
                entry.runtime_data.vehicle_soc = SimpleNamespace(async_set_target=AsyncMock())
                numbers, sensors = {}, {}
                await setup_numbers(hass, entry, lambda items: numbers.update(collect(items)))
                await setup_sensors(hass, entry, lambda items: sensors.update(collect(items)))
                assert numbers["soc_goal"].available
                legacy = deepcopy(service.snapshot())
                legacy["control"].pop("supports_soc_goal")
                legacy["control"].pop("soc_goal")
                remote.apply_snapshot(legacy)
                assert not wrapper.supports_soc_goal
                assert not numbers["soc_goal"].available
                assert not sensors["soc_goal_status"].available
                with pytest.raises(ServiceValidationError) as error:
                    await wrapper.async_set_soc_goal(80, source=SOURCE)
                assert error.value.translation_key == "soc_goal_not_supported"
                await direct_client.async_close()
        finally:
            await service.stop()

    asyncio.run(run())


def test_read_only_remote_accepts_vehicle_metadata_during_outage_but_never_a_target(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                wrapper = RemoteControl(
                    remote, diagnostics=service.diagnostics, options=service.control.options,
                    state=lambda: service.data, request_refresh=AsyncMock(),
                )
                service._success = False
                remote.apply_snapshot(service.snapshot(), require_available=False)
                before_reads = list(transport.reads)
                await wrapper.async_configure_soc(SOURCE)
                await wrapper.async_observe_soc(85, 70, source=SOURCE)
                assert wrapper.soc_goal.maximum_percent == 70
                assert wrapper.soc_goal.target_percent == 0
                assert transport.reads == before_reads
                assert transport.writes == []
                with pytest.raises(HomeAssistantError) as unavailable:
                    await wrapper.async_set_soc_goal(70, source=SOURCE, soc=85)
                assert unavailable.value.translation_key == "service_unavailable"
                service._success = True
                remote.apply_snapshot(service.snapshot())
                with pytest.raises(ServiceValidationError) as error:
                    await wrapper.async_set_soc_goal(70, source=SOURCE, soc=85)
                assert error.value.translation_key == "read_only_mode"
                assert transport.writes == []
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("mutate", [
    lambda control: control.update(supports_soc_goal=1),
    lambda control: control.update(soc_goal=None),
    lambda control: control["soc_goal"].update(source=True),
    lambda control: control["soc_goal"].update(target_percent=True),
    lambda control: control["soc_goal"].update(maximum_percent=0),
    lambda control: control["soc_goal"].update(target_percent=float("nan")),
    lambda control: control["soc_goal"].update(status="unknown"),
    lambda control: control["soc_goal"].update(unexpected="value"),
])
def test_invalid_remote_vehicle_snapshot_is_rejected_atomically(tmp_path, mutate):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://localhost", TOKEN)
                remote.apply_snapshot(service.snapshot())
                previous = remote.write_state.soc_goal.snapshot()
                raw = deepcopy(service.snapshot())
                mutate(raw["control"])
                before = current_writes(transport)
                with pytest.raises(ServiceConnectionError, match="invalid service snapshot"):
                    remote.apply_snapshot(raw)
                assert not remote.connected
                assert remote.write_state.soc_goal.snapshot() == previous
                assert current_writes(transport) == before
        finally:
            await service.stop()

    asyncio.run(run())
