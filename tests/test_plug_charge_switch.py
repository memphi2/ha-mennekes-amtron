"""A device switch applies persistent permission without restarting the bus owner."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.plug_charge import PlugChargeObserver
from custom_components.mennekes_amtron_rtu.switch import AmtronPlugChargeSwitch
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import build_runtime
from tests.test_home_assistant_smoke import SERIAL_NUMBER, _boot
from tests.test_modbus_service import current_writes, service_fixture
from tests.test_service_reauth import add_entry
from tests.test_soc_goal_ha_lifecycle import _entry


@pytest.mark.parametrize("remote", [False, True])
def test_ha_switch_applies_persists_and_preserves_pause_without_reloading(tmp_path, remote):
    async def run():
        async with AsyncExitStack() as stack:
            if remote:
                service, transport, _clock = await service_fixture(tmp_path / "app")
                stack.push_async_callback(service.stop)
                server = await stack.enter_async_context(TestServer(create_app(service)))
            else:
                transport = FakeModbusClient(device_bank())
                stack.enter_context(patch(
                    "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
                    return_value=transport,
                ))
            hass = await _boot(tmp_path / "ha")
            stack.push_async_callback(hass.async_stop)
            entry = await add_entry(hass, str(server.make_url("/"))) if remote else await _entry(hass)
            if not remote:
                hass.config_entries.async_update_entry(entry, options={
                    "control_mode": "master", "scan_interval_seconds": 300,
                    "measurement_interval_seconds": 300,
                })
                await hass.async_block_till_done()
            runtime = entry.runtime_data
            registry = er.async_get(hass)
            switch = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_virtual_plug_charge")
            pause = registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL_NUMBER}_charging_paused")
            assert switch is not None
            assert pause is not None
            assert registry.async_get(switch).entity_category is EntityCategory.CONFIG
            assert hass.states.get(switch).state == "off"
            assert runtime.plug_charge.status == "disabled"
            assert runtime.plug_charge._evidence_unsubscribe is None
            if not remote:
                # Simulate the startup guard having elapsed before pausing.
                runtime.client.write_state.mode_change_at = None
                runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
            await hass.services.async_call("switch", "turn_on", {"entity_id": pause}, blocking=True)
            await hass.async_block_till_done()
            assert runtime.control.pause_requested is True
            before_current = current_writes(transport)
            close_calls = transport.close_calls
            for action, enabled in (("turn_on", True), ("turn_off", False), ("turn_on", True)):
                await hass.services.async_call("switch", action, {"entity_id": switch}, blocking=True)
                await hass.async_block_till_done()
                assert entry.runtime_data is runtime
                assert transport.close_calls == close_calls
                assert runtime.client.connected
                assert entry.options["virtual_plug_charge"] is enabled
                assert runtime.control.plug_charge["enabled"] is enabled
                assert hass.states.get(switch).state == ("on" if enabled else "off")
                assert runtime.control.pause_requested is True
                assert transport.bank[R.CHARGING_RELEASE.address] == 0
                assert current_writes(transport) == before_current
                if not enabled:
                    assert runtime.plug_charge.status == "disabled"
                    assert runtime.plug_charge._evidence_unsubscribe is None
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            assert hass.states.get(switch).state == "on"
            assert entry.runtime_data.control.plug_charge["enabled"]
            assert entry.runtime_data.control.pause_requested is True
    asyncio.run(run())


async def switch_fixture(monkeypatch):
    hass, entry, transport = await build_runtime()
    runtime = entry.runtime_data
    runtime.entry_data = dict(entry.data)
    observer = PlugChargeObserver(hass, entry)
    runtime.plug_charge = observer
    monkeypatch.setattr(observer._evidence, "subscribe", lambda _callback: lambda: None)
    return hass, entry, transport, AmtronPlugChargeSwitch(entry, runtime.coordinator)


@pytest.mark.parametrize("change", ["options", "data"])
def test_concurrent_configuration_save_is_preserved_and_still_reloads(monkeypatch, change):
    async def run():
        hass, entry, _transport, switch = await switch_fixture(monkeypatch)
        runtime = entry.runtime_data
        original = runtime.control.async_configure_plug_charge
        async def configure(*args, **kwargs):
            await original(*args, **kwargs)
            if change == "options":
                entry.options = {**entry.options, "current_limit": 7}
            else:
                entry.data = {**entry.data, "port": "/dev/changed"}
        monkeypatch.setattr(runtime.control, "async_configure_plug_charge", configure)
        try:
            await switch.async_turn_on()
            assert entry.options["virtual_plug_charge"]
            assert entry.options.get("current_limit") == (7 if change == "options" else None)
            assert not runtime.plug_charge.options_applied()
            await integration.async_reload_entry(hass, entry)
            assert hass.config_entries.reloaded == [entry.entry_id]
        finally:
            await runtime.plug_charge.async_stop()
            await runtime.control.async_shutdown()
            await runtime.client.async_close()
    asyncio.run(run())


def test_failed_gate_change_is_not_saved_as_success(monkeypatch):
    async def run():
        _hass, entry, _transport, switch = await switch_fixture(monkeypatch)
        runtime = entry.runtime_data
        monkeypatch.setattr(runtime.control, "async_configure_plug_charge", AsyncMock(side_effect=HomeAssistantError("unreachable")))
        try:
            with pytest.raises(HomeAssistantError):
                await switch.async_turn_on()
            assert "virtual_plug_charge" not in entry.options
            assert not switch.is_on
            assert not runtime.plug_charge.options_applied()
        finally:
            await runtime.plug_charge.async_stop()
            await runtime.control.async_shutdown()
            await runtime.client.async_close()
    asyncio.run(run())


@pytest.mark.parametrize("changed", ["unloaded", "replaced", "profile", "missing"])
def test_stale_switch_cannot_configure_a_replaced_owner_or_changed_profile(monkeypatch, changed):
    async def run():
        _hass, entry, transport, switch = await switch_fixture(monkeypatch)
        runtime, observer = entry.runtime_data, entry.runtime_data.plug_charge
        if changed == "unloaded":
            await observer.async_stop()
        elif changed == "replaced":
            runtime.plug_charge = PlugChargeObserver(_hass, entry)
        elif changed == "missing":
            runtime.plug_charge = None
        else:
            entry.options = {**entry.options, "vehicle_profiles": {"a" * 32: {
                "name": "Other", "provider": "none", "device_id": "", "options": {},
            }}}
        try:
            with pytest.raises(ServiceValidationError):
                await (observer.async_set_enabled(True) if changed == "replaced" else switch.async_turn_on())
            assert not transport.writes
            assert "virtual_plug_charge" not in entry.options
        finally:
            await observer.async_stop()
            await runtime.control.async_shutdown()
            await runtime.client.async_close()
    asyncio.run(run())
