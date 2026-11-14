"""Home Assistant starts, stops and reads the app's orders through its real API."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu import services
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.service_weekly_http import (
    create_weekly_app,
)
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from custom_components.mennekes_amtron_rtu.sensor import async_setup_entry
from custom_components.mennekes_amtron_rtu.service_orders import ServiceOrders
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect
from tests.test_services import _patch_registry
from tests.test_weekly_controller import NOW, Harness

TOKEN = "orders-api-private-test-credential"  # noqa: S105 - fixture


async def _with_app(tmp_path, body, *, app=None):
    """Run ``body`` with the orders of a real app behind a real HTTP client."""
    h = Harness(tmp_path)
    await h.initialize()
    service = SimpleNamespace(weekly=h.weekly, jobs=h.jobs, config=SimpleNamespace(token=TOKEN))
    notified = []
    async with TestServer(app or create_weekly_app(service)) as server, ClientSession() as session:
        client = RemoteClient(session, str(server.make_url("/")), TOKEN)
        await body(h, ServiceOrders(client, lambda: notified.append(True)), notified)


def _key(error) -> str:
    return error.value.translation_key


def test_a_job_is_started_boosted_stopped_and_cleared_in_one_step_each(tmp_path):
    async def body(h, orders, notified):
        await orders.async_refresh()
        assert orders.jobs["job"] is None
        await orders.async_start_job(
            start=NOW + timedelta(hours=1), finish=NOW + timedelta(hours=3),
            current=10, energy=7.5, reserve=1,
        )
        job = orders.jobs["job"]
        assert (job["state"], job["current_a"], job["energy_kwh"], job["reserve_kwh"]) == ("charging", 10.0, 7.5, 1.0)
        assert h.jobs.job is not None
        assert notified
        # A second order is refused with the app's own reason.
        with pytest.raises(HomeAssistantError) as error:
            await orders.async_start_job(
                start=NOW, finish=NOW + timedelta(hours=1), current=6, energy=1,
            )
        assert _key(error) == "job_exists"
        assert orders.jobs["job"]["id"] == job["id"]

        # The reserve is charged at once, so there is nothing to boost.
        await orders.async_stop_job()
        assert orders.jobs["job"]["state"] == "stopped"
        with pytest.raises(HomeAssistantError) as error:
            await orders.async_start_boost(minutes=30)
        assert _key(error) == "job_boost_unavailable"
        h.change(current_a=1)
        await orders.async_clear_job()
        assert orders.jobs["job"] is None

        await orders.async_start_job(
            start=NOW + timedelta(hours=1), finish=NOW + timedelta(hours=3), current=10, energy=5,
        )
        assert orders.jobs["boost_available"] is True
        await orders.async_start_boost(minutes=30)
        assert orders.jobs["job"]["boost_until"] == (NOW + timedelta(minutes=30)).isoformat()
        await orders.async_cancel_boost()
        assert orders.jobs["job"]["boost_until"] is None

    asyncio.run(_with_app(tmp_path, body))


def test_guest_charging_starts_now_for_a_limited_time(tmp_path):
    async def body(h, orders, _notified):
        await orders.async_start_guest(minutes=90, current=8, energy=6)
        job = orders.jobs["job"]
        assert job["kind"] == "guest"
        assert job["start_at"] == NOW.isoformat()
        assert job["finish_at"] == (NOW + timedelta(minutes=90)).isoformat()
        # Without a car nothing is created.
        await orders.async_stop_job()
        h.change(current_a=1)
        await orders.async_clear_job()
        h.change(present=False)
        with pytest.raises(HomeAssistantError) as error:
            await orders.async_start_guest(minutes=90, current=8, energy=6)
        assert _key(error) == "job_not_permitted"
        assert orders.jobs["job"] is None

    asyncio.run(_with_app(tmp_path, body))


def test_a_weekly_plan_is_activated_by_its_name_and_deactivated(tmp_path):
    async def body(h, orders, _notified):
        await h.save()
        with pytest.raises(ServiceValidationError) as error:
            await orders.async_activate_weekly(plan="other car")
        assert _key(error) == "weekly_plan_unknown"
        assert error.value.translation_placeholders == {"plan": "other car"}
        await orders.async_activate_weekly(plan=" Fixture ")
        assert orders.weekly["active"]["vehicle_profile"] == "fixture"
        assert orders.weekly["job"] is not None
        with pytest.raises(HomeAssistantError) as error:
            await orders.async_activate_weekly(plan="fixture")
        assert _key(error) == "weekly_already_active"
        await orders.async_deactivate_weekly()
        assert orders.weekly["active"] is None
        # Deactivating again changes nothing and is no failure.
        await orders.async_deactivate_weekly()
        assert orders.weekly["active"] is None

    asyncio.run(_with_app(tmp_path, body))


def test_an_app_without_orders_leaves_the_status_unknown_and_names_the_failure(tmp_path):
    async def body(_h, orders, _notified):
        orders.jobs = orders.weekly = {"stale": True}
        await orders.async_refresh()
        assert orders.jobs is None
        assert orders.weekly is None
        for call in (orders.async_stop_job(), orders.async_deactivate_weekly(),
                     orders.async_start_guest(minutes=5, current=6, energy=1)):
            with pytest.raises(HomeAssistantError) as error:
                await call
            assert _key(error) == "service_order_failed"

    asyncio.run(_with_app(tmp_path, body, app=web.Application()))


class _Orders:
    """Records what an action asks for; the sensors read its status."""

    def __init__(self):
        self.calls = []
        self.jobs = None
        self.weekly = None

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
            await act("stop_charging_job")
        assert _key(error) == "app_required"

        orders = entry.runtime_data.orders = _Orders()
        await act("start_charging_job", finish="2026-11-16T08:00:00+00:00", current=10, energy="7.5")
        name, values = orders.calls.pop()
        assert name == "async_start_job"
        assert values["finish"].isoformat() == "2026-11-16T08:00:00+00:00"
        assert values["start"].tzinfo is not None
        assert (values["current"], values["energy"], values["reserve"]) == (10.0, 7.5, 0)
        await act("start_charging_job", start="2026-11-16T06:00:00+01:00",
                  finish="2026-11-16T08:00:00+01:00", current=6, energy=1, reserve=0.5)
        values = orders.calls.pop()[1]
        assert values["start"].isoformat() == "2026-11-16T05:00:00+00:00"
        assert values["reserve"] == 0.5
        await act("start_guest_charging", duration=90, current=8, energy=6)
        assert orders.calls.pop() == ("async_start_guest", {"minutes": 90, "current": 8.0, "energy": 6.0})
        await act("start_boost", duration=30)
        assert orders.calls.pop() == ("async_start_boost", {"minutes": 30})
        await act("activate_weekly_plan", plan="Family car")
        assert orders.calls.pop() == ("async_activate_weekly", {"plan": "Family car"})
        for name, method in (("stop_charging_job", "async_stop_job"), ("clear_charging_job", "async_clear_job"),
                             ("cancel_boost", "async_cancel_boost"),
                             ("deactivate_weekly_plan", "async_deactivate_weekly")):
            await act(name)
            assert orders.calls.pop() == (method, {})

        # With one wallbox the target can be left out; with two it cannot.
        handler, schema = hass.services.registered[(DOMAIN, "cancel_boost")]
        await handler(SimpleNamespace(context=Context(), data=schema({})))
        assert orders.calls.pop() == ("async_cancel_boost", {})
        other = FakeConfigEntry(entry_id="entry-2")
        other.runtime_data = entry.runtime_data
        hass.config_entries.entries["entry-2"] = other
        with pytest.raises(ServiceValidationError) as error:
            await handler(SimpleNamespace(context=Context(), data=schema({})))
        assert _key(error) == "wallbox_target_required"
        assert orders.calls == []

    asyncio.run(run())


@pytest.mark.parametrize(("name", "fields"), [
    ("start_charging_job", {"finish": "2026-11-16T08:00:00+00:00", "current": 5.9, "energy": 1}),
    ("start_charging_job", {"finish": "2026-11-16T08:00:00+00:00", "current": 6, "energy": 0}),
    ("start_charging_job", {"current": 6, "energy": 1}),
    ("start_guest_charging", {"duration": 1441, "current": 6, "energy": 1}),
    ("start_boost", {"duration": 121}),
    ("activate_weekly_plan", {"plan": ""}),
    ("stop_charging_job", {"force": True}),
])
def test_actions_refuse_values_the_app_would_refuse(name, fields):
    async def run():
        hass, _entry, _transport = await build_runtime()
        services.async_setup_services(hass)
        _handler, schema = hass.services.registered[(DOMAIN, name)]
        with pytest.raises(Exception, match=r"(?s).+"):
            schema({"device_id": "dev", **fields})

    asyncio.run(run())


def test_the_order_sensors_exist_only_with_the_app_and_show_its_status():
    async def run():
        hass, entry, _transport = await build_runtime()
        direct: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda entities: direct.update(collect(entities)))
        assert not {"charging_job", "weekly_plan"} & set(direct)

        orders = entry.runtime_data.orders = _Orders()
        added: dict[str, object] = {}
        await async_setup_entry(hass, entry, lambda entities: added.update(collect(entities)))
        job, weekly = added["charging_job"], added["weekly_plan"]
        # Before the first status, or with an app that has no orders.
        assert not job.available
        assert not weekly.available
        assert job.native_value is None
        assert job.extra_state_attributes is None

        orders.jobs = {"job": None, "boost_available": False}
        orders.weekly = {"active": None, "plans": [{"plan": {"vehicle_profile": "Family car"}}, "damaged"]}
        assert job.available
        assert job.native_value == "none"
        assert job.extra_state_attributes == {"boost_available": False}
        assert weekly.native_value == "inactive"
        assert weekly.extra_state_attributes == {"plans": ["Family car"]}

        orders.jobs = {"boost_available": True, "job": {
            "state": "scheduled", "reason": "scheduled", "kind": "guest",
            "start_at": "2026-11-16T06:00:00+00:00", "finish_at": "not a time",
            "current_a": 10.0, "energy_kwh": 7.5, "delivered_kwh": 0.0, "boost_until": None,
        }}
        orders.weekly = {"plans": None, "active": {"vehicle_profile": "Family car", "until": "2026-11-23T05:00:00+00:00"}}
        assert job.native_value == "scheduled"
        attributes = job.extra_state_attributes
        assert attributes["start"].isoformat() == "2026-11-16T06:00:00+00:00"
        assert attributes | {"start": None} == {
            "boost_available": True, "kind": "guest", "start": None, "current": 10.0,
            "energy": 7.5, "delivered": 0.0, "reason": "scheduled",
        }
        assert weekly.native_value == "active"
        assert weekly.extra_state_attributes["plan"] == "Family car"
        assert weekly.extra_state_attributes["until"].year == 2026
        # A state this version does not know is no state.
        orders.jobs = {"job": {"state": "invented"}}
        assert job.native_value is None

    asyncio.run(run())
