"""Recover switch intent after partial gate changes and lost HTTP replies."""

import asyncio
from contextlib import AsyncExitStack

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.plug_charge import plug_charge_policy
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes, service_fixture
from tests.test_plug_charge_switch import switch_fixture
from tests.test_service_reauth import add_entry


@pytest.mark.parametrize("enabled", [False, True])
def test_direct_partial_gate_failure_keeps_observer_and_saved_setting_consistent(monkeypatch, enabled):
    async def run():
        _hass, entry, transport, switch = await switch_fixture(monkeypatch)
        runtime = entry.runtime_data
        try:
            if not enabled:
                await switch.async_turn_on()
                runtime.client.write_state.limiter.forget(R.CHARGING_RELEASE.key)
            transport.bank[R.CHARGING_RELEASE.address] = 1
            transport.write_exceptions[R.CHARGING_RELEASE.address] = "error"
            before_current = current_writes(transport)
            with pytest.raises(HomeAssistantError):
                await (switch.async_turn_on() if enabled else switch.async_turn_off())
            # The real controller changed its logical gate before the bus
            # failure. Reporting the failure must not strand an on gate with
            # an off observer or restore an old setting after a restart.
            assert switch.is_on is enabled
            assert entry.options["virtual_plug_charge"] is enabled
            assert (runtime.plug_charge.status == "disabled") is not enabled
            assert (runtime.plug_charge._evidence_unsubscribe is not None) is enabled
            assert current_writes(transport) == before_current
        finally:
            await runtime.plug_charge.async_stop()
            await runtime.control.async_shutdown()
            await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_lost_service_reply_reconciles_on_next_normal_poll_without_replaying_command(tmp_path, enabled):
    async def run():
        async with AsyncExitStack() as stack:
            service, transport, _clock = await service_fixture(tmp_path / "app")
            stack.push_async_callback(service.stop)
            drop_next = False
            requests = []

            @web.middleware
            async def lose_completed_reply(request, handler):
                nonlocal drop_next
                requests.append((request.method, request.path))
                response = await handler(request)
                if drop_next and request.method == "POST" and request.path == "/v2/command":
                    drop_next = False
                    # The app accepted and saved the command, but HA gets no
                    # usable response snapshot (e.g. an intermediary fails).
                    return web.Response(status=503, text="response unavailable")
                return response

            app = create_app(service)
            app.middlewares.insert(0, lose_completed_reply)
            server = await stack.enter_async_context(TestServer(app))
            hass = await _boot(tmp_path / "ha")
            stack.push_async_callback(hass.async_stop)
            entry = await add_entry(hass, str(server.make_url("/")))
            runtime = entry.runtime_data
            switch = er.async_get(hass).async_get_entity_id(
                "switch", DOMAIN, f"{SERIAL_NUMBER}_virtual_plug_charge"
            )
            assert switch is not None
            if not enabled:
                await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
                await hass.async_block_till_done()
            requests.clear()
            before_current = current_writes(transport)
            drop_next = True
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "switch", "turn_on" if enabled else "turn_off", {"entity_id": switch}, blocking=True
                )
            await hass.async_block_till_done()
            assert service.control.plug_charge["enabled"] is enabled
            assert entry.options.get("virtual_plug_charge", False) is not enabled
            assert not runtime.client.connected
            assert requests == [("POST", "/v2/command")]

            # Simulate the next scheduled state read. Recovery is observation
            # only, never a second physical configuration command.
            await runtime.coordinator.async_refresh()
            await hass.async_block_till_done()
            assert entry.options["virtual_plug_charge"] is enabled
            assert entry.runtime_data is runtime
            assert hass.states.get(switch).state == ("on" if enabled else "off")
            assert (runtime.plug_charge.status == "disabled") is not enabled
            # The app's charging job is read along with its state.
            assert requests == [("POST", "/v2/command"), ("GET", "/v2/state"), ("GET", "/v2/jobs")]
            assert current_writes(transport) == before_current

    asyncio.run(run())


def test_existing_service_gate_can_be_disabled_after_app_becomes_read_only(tmp_path):
    async def run():
        async with AsyncExitStack() as stack:
            service, transport, _clock = await service_fixture(tmp_path / "app", read_only=True)
            stack.push_async_callback(service.stop)
            service.client.write_state.plug_charge.configure(True, plug_charge_policy({}))
            server = await stack.enter_async_context(TestServer(create_app(service)))
            hass = await _boot(tmp_path / "ha")
            stack.push_async_callback(hass.async_stop)
            entry = await add_entry(hass, str(server.make_url("/")))
            switch = er.async_get(hass).async_get_entity_id(
                "switch", DOMAIN, f"{SERIAL_NUMBER}_virtual_plug_charge"
            )
            assert switch is not None
            assert entry.runtime_data.control.read_only
            assert hass.states.get(switch).state == "on"
            await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
            await hass.async_block_till_done()
            assert entry.options["virtual_plug_charge"] is False
            assert service.control.plug_charge["enabled"] is False
            assert hass.states.get(switch).state == "unavailable"
            assert transport.writes == []

    asyncio.run(run())
