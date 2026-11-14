"""Home Assistant starts, stops and reads the app's orders through its real API."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import services
from custom_components.mennekes_amtron_rtu.device.charge_job_controller import (
    ChargeJobController,
    JobInputs,
)
from custom_components.mennekes_amtron_rtu.device.charge_job_store import ChargeJobStore
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_job_http import create_job_app
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.sensor import async_setup_entry
from custom_components.mennekes_amtron_rtu.service_orders import ServiceOrders
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect
from tests.test_services import _patch_registry

TOKEN = "orders-api-private-test-credential"  # noqa: S105 - fixture
NOW = datetime(2026, 11, 16, 5, tzinfo=UTC)
SCOPE = "b" * 64
SERIAL = "ABC123456789"


class Harness:
    """The app's real job controller over a wallbox that only exists here."""

    def __init__(self, path):
        self.sample = JobInputs(NOW, True, True, SCOPE, 100.0, True, True, False, 8.0, 16.0)
        self.calls = []
        self.jobs = ChargeJobController(
            ChargeJobStore(path / "charge-job.json", "fixture"), lambda: self.sample, self.apply,
        )

    def change(self, **changes):
        self.sample = replace(self.sample, **changes)

    async def apply(self, value, owner, deadline):
        self.calls.append((value, owner, deadline))
        self.change(current_a=1 if value is None else value)


def _key(error) -> str:
    return error.value.translation_key


def test_guest_button_uses_real_snapshot_and_identity_bound_command(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
        DepartureModbusService,
    )
    from tests import test_service_charge_runtime as runtime
    async def run():
        monkeypatch.setattr(runtime, "JobModbusService", DepartureModbusService)
        async with (
            runtime.job_service(tmp_path) as (service, transport, clock),
            TestServer(create_job_app(service)) as server, ClientSession() as session,
        ):
            client = RemoteClient(session, str(server.make_url("/")), runtime.TOKEN)
            client.bind_identity(service.identity.serial_number)
            await client.update()
            orders = ServiceOrders(client, client.update)
            await service.control.async_pause_charging()
            clock[0] += 1000
            await orders.async_start_guest()
            assert orders.jobs["job"]["state"] == "charging"
            assert runtime.current_writes(transport)[-1] >= 6
            with pytest.raises(HomeAssistantError):
                await orders.async_start_guest()
            client._connected = False
            with pytest.raises(HomeAssistantError) as error:
                await orders.async_start_guest()
            assert _key(error) == "service_unavailable"
    asyncio.run(run())


class _Orders:
    """Records what an action asks for; the sensors read its status."""

    def __init__(self):
        self.calls = []
        self.jobs = None

    def __getattr__(self, name):
        async def call(**values):
            self.calls.append((name, values))
        return call


def test_every_action_reaches_the_orders_of_an_app_connection(monkeypatch):
    async def run():
        hass, entry, _transport = await build_runtime()
        _patch_registry(monkeypatch, {"dev": SimpleNamespace(config_entry_id=entry.entry_id)})
        services.async_setup_services(hass)

        async def act(name, **fields):
            handler, schema = hass.services.registered[(DOMAIN, name)]
            await handler(SimpleNamespace(context=Context(), data=schema({"device_id": "dev", **fields})))

        # A direct connection has no app to carry orders out.
        with pytest.raises(ServiceValidationError) as error:
            await act("start_guest_charging")
        assert _key(error) == "app_required"

        orders = entry.runtime_data.orders = _Orders()
        await act("start_guest_charging")
        assert orders.calls.pop() == ("async_start_guest", {})
        assert not {"start_charging_job", "start_boost", "cancel_boost"} & services.ORDER_ACTIONS.keys()

        # With one wallbox the target can be left out; with two it cannot.
        handler, schema = hass.services.registered[(DOMAIN, "start_guest_charging")]
        await handler(SimpleNamespace(context=Context(), data=schema({})))
        assert orders.calls.pop() == ("async_start_guest", {})
        other = FakeConfigEntry(entry_id="entry-2")
        other.runtime_data = entry.runtime_data
        hass.config_entries.entries["entry-2"] = other
        with pytest.raises(ServiceValidationError) as error:
            await handler(SimpleNamespace(context=Context(), data=schema({})))
        assert _key(error) == "wallbox_target_required"
        assert orders.calls == []

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


def test_the_job_sensor_exists_only_with_the_app_and_shows_its_status():
    async def run():
        hass, entry, _transport = await build_runtime()
        direct: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda entities: direct.update(collect(entities)))
        assert "guest_charging" not in direct

        orders = entry.runtime_data.orders = _Orders()
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda entities: added.update(collect(entities)))
        job = added["guest_charging"]
        # Before the first status, or with an app that has no orders.
        assert not job.available
        assert job.native_value is None
        assert job.extra_state_attributes is None

        orders.jobs = {"job": None, "boost_available": False}
        assert job.available
        assert job.native_value == "none"
        assert job.extra_state_attributes is None

        orders.jobs = {"boost_available": True, "job": {
            "state": "scheduled", "reason": "scheduled", "kind": "guest",
            "start_at": "2026-11-16T06:00:00+00:00", "finish_at": "not a time",
            "current_a": 10.0, "energy_kwh": 7.5, "delivered_kwh": 0.04999, "boost_until": None,
        }}
        assert job.native_value == "scheduled"
        attributes = job.extra_state_attributes
        assert attributes["start"].isoformat() == "2026-11-16T06:00:00+00:00"
        assert attributes | {"start": None} == {
            "start": None, "current": 10.0,
            "energy": 7.5, "delivered": 0.0, "reason": "scheduled",
        }
        # A state this version does not know is no state.
        orders.jobs = {"job": {"state": "invented"}}
        assert job.native_value is None

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
        orders = SimpleNamespace(jobs={}, async_start_guest=AsyncMock())
        entry.runtime_data.orders = orders
        assert button.available
        await button.async_press()
        orders.async_start_guest.assert_awaited_once()
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
        orders = ServiceOrders(client, AsyncMock())
        with pytest.raises(HomeAssistantError) as error:
            await orders.async_start_guest()
        assert _key(error) == "service_unavailable"
        client.execute.assert_not_awaited()
    asyncio.run(run())
