"""The active bus owner controls the vehicle's phase-switch grace period."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import probatio
import pytest
from aiohttp import ClientSession

from custom_components.mennekes_amtron_rtu import _control_options
from custom_components.mennekes_amtron_rtu.config_schemas import options_schema
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_PHASE_SWITCH_GRACE_SECONDS,
)
from custom_components.mennekes_amtron_rtu.device.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_options_flow import Flow

SERVICE_OPTIONS = {"serial_port": "/dev/fake", "token": TOKEN}


@pytest.mark.parametrize("seconds", [0, 5, 13, 30])
def test_direct_options_store_whole_seconds_and_pass_them_to_control(seconds):
    async def run():
        form = await Flow(FakeConfigEntry()).async_step_init()
        submitted = form["data_schema"]({CONF_PHASE_SWITCH_GRACE_SECONDS: float(seconds)})
        result = await Flow(FakeConfigEntry()).async_step_init(submitted)
        stored = result["data"]
        assert type(stored[CONF_PHASE_SWITCH_GRACE_SECONDS]) is int
        entry = FakeConfigEntry(options=stored)
        assert _control_options(entry).phase_switch_grace_seconds == seconds
        defaults = {
            marker.schema: marker.default()
            for marker in options_schema(stored, max_current=16).schema
        }
        assert defaults[CONF_PHASE_SWITCH_GRACE_SECONDS] == seconds

    asyncio.run(run())


def test_grace_defaults_to_five_seconds_in_direct_and_service_configuration():
    assert options_schema({}, max_current=16)({})[CONF_PHASE_SWITCH_GRACE_SECONDS] == 5
    assert _control_options(FakeConfigEntry()).phase_switch_grace_seconds == 5
    assert ServerConfig.from_dict(SERVICE_OPTIONS).phase_switch_grace_seconds == 5


@pytest.mark.parametrize("value", [-1, 31, True, None, "5", float("nan"), float("inf")])
def test_core_rejects_invalid_grace_even_without_a_configuration_flow(value: Any):
    with pytest.raises(ValueError, match="phase_switch_grace_seconds"):
        ControlOptions(phase_switch_grace_seconds=value)


@pytest.mark.parametrize("value", [-1, 31, 1.5, True, None, "5", float("nan"), float("inf")])
def test_invalid_direct_grace_values_are_rejected(value: Any):
    with pytest.raises(probatio.Invalid):
        options_schema({}, max_current=16)({CONF_PHASE_SWITCH_GRACE_SECONDS: value})


@pytest.mark.parametrize("value", [-1, 31, 1.5, 5.0, True, None, "5", float("nan"), float("inf")])
def test_service_grace_requires_an_integer_within_range(value: Any):
    with pytest.raises(ValueError, match="phase_switch_grace_seconds"):
        ServerConfig.from_dict({**SERVICE_OPTIONS, "phase_switch_grace_seconds": value})


def test_service_integration_options_hide_and_discard_local_grace_setting():
    async def run():
        flow = Flow(FakeConfigEntry(
            data={"server_url": "http://service:8765"},
            options={CONF_PHASE_SWITCH_GRACE_SECONDS: 20},
        ))
        form = await flow.async_step_init()
        schema = form["data_schema"]
        assert CONF_PHASE_SWITCH_GRACE_SECONDS not in {
            marker.schema for marker in schema.schema
        }
        submitted = {**schema({}), CONF_PHASE_SWITCH_GRACE_SECONDS: 20}
        result = await flow.async_step_service(submitted)
        assert CONF_PHASE_SWITCH_GRACE_SECONDS not in result["data"]

    asyncio.run(run())


@pytest.mark.parametrize("seconds", [0, 5, 13, 30])
def test_service_owns_grace_and_publishes_authoritative_configuration(tmp_path, seconds):
    async def run():
        config = ServerConfig.from_dict({
            **SERVICE_OPTIONS, "phase_switch_grace_seconds": seconds,
        })
        transport = FakeModbusClient(device_bank())
        client = MennekesModbusClient(config.connection, client_factory=lambda _: transport)
        service = ModbusService(config, tmp_path / "intent.json", client=client)
        try:
            await service._initialize()
            assert service.control.options.phase_switch_grace_seconds == seconds
            payload = service.snapshot()
            assert payload["control"]["phase_switch_grace_seconds"] == seconds
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://service:8765", TOKEN)
                remote.apply_snapshot(payload)
                assert remote.connected
                payload["control"].pop("phase_switch_grace_seconds")
                remote.apply_snapshot(payload)
                assert remote.connected
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("value", [-1, 31, 1.5, True, None, "5", float("nan"), float("inf")])
def test_malformed_optional_service_grace_invalidates_snapshot(tmp_path, value):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://service:8765", TOKEN)
                remote.apply_snapshot(service.snapshot())
                payload = copy.deepcopy(service.snapshot())
                payload["control"]["phase_switch_grace_seconds"] = value
                with pytest.raises(ServiceConnectionError, match=r"control\.phase_switch_grace_seconds"):
                    remote.apply_snapshot(payload)
                assert not remote.connected
        finally:
            await service.stop()

    asyncio.run(run())
