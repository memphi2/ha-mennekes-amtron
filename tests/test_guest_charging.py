"""Home Assistant starts and reads guest charging through the real service API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import services
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.guest_charging import GuestCharging
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.ha_fakes import FakeConfigEntry, build_runtime
from tests.test_services import _patch_registry

TOKEN = "guest-api-private-test-credential"  # noqa: S105 - fixture
NOW = datetime(2026, 11, 16, 5, tzinfo=UTC)
SCOPE = "b" * 64
SERIAL = "ABC123456789"


def _key(error) -> str:
    return error.value.translation_key


def test_guest_button_uses_real_snapshot_and_identity_bound_command(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
        DepartureModbusService,
    )
    from tests import service_guest_fixture as runtime
    async def run():
        monkeypatch.setattr(runtime, "GuestModbusService", DepartureModbusService)
        async with (
            runtime.guest_service(tmp_path) as (service, transport, clock),
            TestServer(create_app(service)) as server, ClientSession() as session,
        ):
            client = RemoteClient(session, str(server.make_url("/")), runtime.TOKEN)
            client.bind_identity(service.identity.serial_number)
            await client.update()
            guest = GuestCharging(client, client.update)
            await service.control.async_pause_charging()
            clock[0] += 1000
            await guest.async_start_guest()
            assert guest.active is True
            assert runtime.current_writes(transport)[-1] >= 6
            await guest.async_start_guest()
            assert guest.active is True
            client._connected = False
            with pytest.raises(HomeAssistantError) as error:
                await guest.async_start_guest()
            assert _key(error) == "service_unavailable"
    asyncio.run(run())


class _Guest:
    """Records what an action asks for; the sensors read its status."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def call(**values):
            self.calls.append((name, values))
        return call


def test_every_action_reaches_the_guest_of_an_app_connection(monkeypatch):
    async def run():
        hass, entry, _transport = await build_runtime()
        _patch_registry(monkeypatch, {"dev": SimpleNamespace(config_entry_id=entry.entry_id)})
        services.async_setup_services(hass)

        async def act(name, **fields):
            handler, schema = hass.services.registered[(DOMAIN, name)]
            await handler(SimpleNamespace(context=Context(), data=schema({"device_id": "dev", **fields})))

        # A direct connection has no app to carry guest out.
        with pytest.raises(ServiceValidationError) as error:
            await act("start_guest_charging")
        assert _key(error) == "app_required"

        guest = entry.runtime_data.guest = _Guest()
        await act("start_guest_charging")
        assert guest.calls.pop() == ("async_start_guest", {})
        assert not {"start_charging_job", "start_boost", "cancel_boost"} & services.GUEST_ACTIONS.keys()

        # With one wallbox the target can be left out; with two it cannot.
        handler, schema = hass.services.registered[(DOMAIN, "start_guest_charging")]
        await handler(SimpleNamespace(context=Context(), data=schema({})))
        assert guest.calls.pop() == ("async_start_guest", {})
        other = FakeConfigEntry(entry_id="entry-2")
        other.runtime_data = entry.runtime_data
        hass.config_entries.entries["entry-2"] = other
        with pytest.raises(ServiceValidationError) as error:
            await handler(SimpleNamespace(context=Context(), data=schema({})))
        assert _key(error) == "wallbox_target_required"
        assert guest.calls == []

    asyncio.run(run())


@pytest.mark.parametrize(("name", "fields"), [
    ("start_guest_charging", {"duration": 60, "current": 5.9, "energy": 1}),
    ("start_guest_charging", {"duration": 60, "current": 6, "energy": 0.04}),
    ("start_guest_charging", {"current": 6, "energy": 1}),
    ("start_guest_charging", {"duration": 1441, "current": 6, "energy": 1}),
    ("start_guest_charging", {"force": True}),
])
def test_actions_refuse_values_the_app_would_refuse(name, fields):
    async def run():
        hass, _entry, _transport = await build_runtime()
        services.async_setup_services(hass)
        _handler, schema = hass.services.registered[(DOMAIN, name)]
        with pytest.raises(Exception, match=r"(?s).+"):
            schema({"device_id": "dev", **fields})

    asyncio.run(run())



def test_single_guest_button_delegates_and_reports_missing_service():
    from unittest.mock import AsyncMock

    from custom_components.mennekes_amtron_rtu.guest_entities import GuestButton
    async def run():
        _hass, entry, _transport = await build_runtime()
        button = GuestButton(entry)
        assert not button.available
        with pytest.raises(ServiceValidationError):
            await button.async_press()
        guest = SimpleNamespace(active=False, async_start_guest=AsyncMock())
        entry.runtime_data.guest = guest
        assert button.available
        await button.async_press()
        guest.async_start_guest.assert_awaited_once()
        entry.runtime_data.coordinator.last_update_success = False
        assert not button.available
        await entry.runtime_data.control.async_shutdown()
    asyncio.run(run())


def test_failed_refresh_prevents_guest_start_and_reports_service_unavailable():
    from unittest.mock import AsyncMock

    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronConnectionError,
    )
    async def run():
        client = SimpleNamespace(connected=True, update=AsyncMock(side_effect=AmtronConnectionError("offline")), execute=AsyncMock())
        guest = GuestCharging(client, AsyncMock())
        with pytest.raises(HomeAssistantError) as error:
            await guest.async_start_guest()
        assert _key(error) == "service_unavailable"
        client.execute.assert_not_awaited()
    asyncio.run(run())


def test_explicit_remote_time_edit_uses_the_service_revision(tmp_path, monkeypatch):
    from datetime import timedelta

    from custom_components.mennekes_amtron_rtu.device.data import WriteDiagnostics
    from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
        DepartureModbusService,
    )
    from custom_components.mennekes_amtron_rtu.remote import (
        ControlOptions,
        RemoteControl,
    )
    from tests import service_guest_fixture as runtime

    async def run():
        monkeypatch.setattr(runtime, "GuestModbusService", DepartureModbusService)
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock), TestServer(
            create_app(service)
        ) as server, ClientSession() as session:
            client = RemoteClient(session, str(server.make_url("/")), runtime.TOKEN)
            await client.update()
            control = RemoteControl(
                client, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
                state=lambda: None, request_refresh=lambda: None,
            )
            deadline = datetime.now(UTC) + timedelta(hours=1)
            await control.async_set_target_time(deadline)
            assert service.guest_deadline == deadline
            await control.async_set_target_time(None)
            assert service.guest_deadline is None
            service._guest_scope = service._connection_sample().scope
            client.apply_snapshot(service.snapshot())
            assert control.guest_active()
    asyncio.run(run())
