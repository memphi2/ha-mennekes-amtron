"""Connection diagnosis must not mistake valid hardware codes for bad tokens."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.capabilities import (
    phase_switch_is_supported,
)
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.fakes import FakeModbusClient, device_bank, put
from tests.test_modbus_service import TOKEN, current_writes, service_fixture


@pytest.mark.parametrize("phase_code", [3, 4, 65535])
def test_unknown_phase_code_keeps_readings_but_disables_phase_switching(tmp_path, phase_code):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        service.identity.phase_options_hw = phase_code
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                assert remote.connected
                assert remote.identity.phase_options_hw == phase_code
                assert not phase_switch_is_supported(remote.identity.phase_options_hw)
                assert remote.snapshot["values"]
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("maximum", [0, 5, 33, 63, -1, 6, 16, 32])
@pytest.mark.parametrize("current_cap", [6, 10])
@pytest.mark.parametrize("previous_app_payload", [False, True])
def test_optional_current_capability_cannot_block_setup_or_lift_command_limits(
    tmp_path, maximum, current_cap, previous_app_payload,
):
    async def run():
        bank = device_bank(charging_current=8)
        put(bank, R.MAX_EVSE_CURRENT, maximum)
        transport = FakeModbusClient(bank)
        client = MennekesModbusClient(
            SerialConfig("/dev/fake"), client_factory=lambda _: transport,
        )
        service = ModbusService(
            ServerConfig(client.config, TOKEN, read_only=False, current_cap=current_cap),
            tmp_path / "intent.json", client=client,
        )
        try:
            await service._initialize()
            expected = float(maximum) if 6 <= maximum <= 32 else None
            assert service.identity.max_evse_current == expected
            if previous_app_payload:
                # Beta 3 publishes this optional capability without normalizing
                # it. Updating HA first must also accept that existing app.
                service.identity.max_evse_current = float(maximum)
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                assert remote.connected
                assert remote.identity.max_evse_current == expected
                assert remote.snapshot["values"][R.MAX_EVSE_CURRENT.key] == float(maximum)
                assert all(address == R.HEARTBEAT.address for address, _ in transport.writes)
                assert current_writes(transport) == []

                # Even an explicit upper-range API request stays inside the
                # configured cap and every usable hardware limit.
                await remote.execute("set_current", value=32)
                limit = min(current_cap, expected) if expected is not None else current_cap
                assert current_writes(transport) == [float(limit)]
                assert remote.write_state.charging_setpoint == float(limit)
                assert remote.snapshot["values"][R.MAX_EVSE_CURRENT.key] == float(maximum)
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["authentication", "not_ready", "snapshot", "json"])
def test_failure_categories_are_precise_and_do_not_expose_response_data(tmp_path, fault):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        try:
            if fault == "not_ready":
                service._success = False
            if fault == "snapshot":
                service.identity.phase_options_hw = -1
            app = create_app(service)
            if fault == "json":
                app = web.Application()

                async def invalid(_request):
                    return web.Response(text="secret-response-body")

                app.router.add_get("/v1/state", invalid)
            async with TestServer(app) as server, ClientSession() as session:
                token = "wrong" * 10 if fault == "authentication" else TOKEN
                remote = RemoteClient(session, str(server.make_url("/")), token)
                with pytest.raises(ServiceConnectionError) as raised:
                    await remote.async_connect()
                assert raised.value.reason == {
                    "authentication": "service_auth_failed", "not_ready": "service_not_ready",
                    "snapshot": "service_invalid_response", "json": "service_invalid_response",
                }[fault]
                if fault == "snapshot":
                    assert "identity.phase_options_hw" in str(raised.value)
                assert TOKEN not in str(raised.value)
                assert "secret-response-body" not in str(raised.value)
        finally:
            await service.stop()

    asyncio.run(run())
