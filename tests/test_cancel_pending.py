"""Cancelling a queue never resumes charging or requires a healthy Modbus bus."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.button import async_setup_entry
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import RequestedPhases
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.service_errors import (
    ServiceConnectionError,
)
from tests.ha_fakes import build_runtime, collect
from tests.test_command_events import outcomes
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_phase_pause_confirmation import _harness


def test_cancel_is_readless_idempotent_and_retains_the_remembered_target():
    async def run():
        harness = Harness()
        control = await harness.connect()
        notified = []
        control._notify_pending = lambda: notified.append(control.pending_action)
        assert control.pending_action is None
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(14)
        assert control.pending_action is not None
        assert control.charging_setpoint == 14
        before = (harness.transport.reads[:], harness.transport.writes[:], harness.refreshes)
        await harness.client.async_close()

        await control.async_cancel_pending_change()
        assert control.pending_action is None
        assert control.pending_until is None
        assert control.charging_setpoint == 14
        assert notified == [None]
        assert outcomes(control.events)[-1] == ("cancelled", "set_current", "user_cancelled")
        events = control.events.snapshot()
        await control.async_cancel_pending_change()
        assert control.events.snapshot() == events
        assert notified == [None]
        if control._flush_task is not None:
            await asyncio.wait_for(control._flush_task, 1)
        assert (harness.transport.reads, harness.transport.writes, harness.refreshes) == before

    asyncio.run(run())


def test_cancelling_phase_wait_does_not_undo_its_applied_pause():
    async def run():
        harness = _harness()
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert control.pending_action == "single_phase"
        assert control.is_paused is True
        writes = harness.transport.writes[:]
        await control.async_cancel_pending_change()
        assert control._phase_change is None
        assert control.is_paused is True
        assert control.pause_requested is True
        assert control.charging_setpoint == 8
        assert harness.transport.writes == writes
        assert harness.written(R.REQUESTED_PHASES) == []
        assert outcomes(control.events)[-1] == ("cancelled", "single_phase", "user_cancelled")
        await control.async_shutdown()

    asyncio.run(run())


def test_cancellation_does_not_interrupt_a_command_already_writing():
    async def run():
        harness = Harness()
        control = await harness.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        original = harness.transport.write_registers

        async def held_write(address, values, **kwargs):
            entered.set()
            await release.wait()
            return await original(address, values, **kwargs)

        harness.transport.write_registers = held_write
        executing = asyncio.create_task(control.async_pause_charging())
        await asyncio.wait_for(entered.wait(), 1)
        cancellation = asyncio.create_task(control.async_cancel_pending_change())
        await asyncio.sleep(0)
        assert not cancellation.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(executing, cancellation), 1)
        assert control.is_paused is True
        assert outcomes(control.events) == [("executed", "pause", "completed")]

    asyncio.run(run())


def test_button_stays_usable_on_failed_poll_but_follows_write_permission():
    async def run():
        hass, entry, transport = await build_runtime()
        buttons = {}
        await async_setup_entry(hass, entry, lambda items: buttons.update(collect(items)))
        button = buttons["cancel_pending_change"]
        control = entry.runtime_data.control
        coordinator = entry.runtime_data.coordinator
        owner = entry.service.control

        async def permission(read_only):
            # The service owns the permission; HA learns it from its next poll.
            entry.service.config = replace(entry.service.config, read_only=read_only)
            owner._options = replace(owner.options, read_only=read_only)
            await coordinator.async_refresh()
            coordinator.last_update_success = False

        try:
            assert button.entity_category is None
            assert not button.available
            await control.async_set_charging_current(10)
            await control.async_set_charging_current(12)
            coordinator.last_update_success = False
            assert button.available
            await permission(True)
            assert not button.available
            with pytest.raises(ServiceValidationError) as error:
                await button.async_press()
            assert error.value.translation_key == "read_only_mode"
            assert owner.pending_action == "set_current"
            await permission(False)
            assert button.available
            writes = transport.writes[:]
            await button.async_press()
            assert transport.writes == writes
            assert owner.pending_action is None
            assert not button.available
        finally:
            await entry.runtime_data.client.async_close()
            await entry.service.stop()

    asyncio.run(run())


def test_service_cancel_is_allowed_when_only_the_wallbox_is_unavailable(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        control = service.control
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(12)
        assert control.pending_action == "set_current"
        service._success = False
        service.refresh = AsyncMock(side_effect=AssertionError("cancel must not poll"))
        before = (transport.reads[:], transport.writes[:])
        payload = {"command": "cancel_pending", "expected_serial_number": service.identity.serial_number}
        try:
            with pytest.raises(DeviceError, match="device_identity_changed"):
                await service.command({**payload, "expected_serial_number": "different"})
            assert control.pending_action == "set_current"
            with pytest.raises(DeviceError, match="service_unavailable"):
                await service.command({**payload, "command": "resume"})
            snapshot = await service.command(payload)
            assert snapshot["available"] is False
            assert snapshot["control"]["pending_action"] is None
            assert control.charging_setpoint == 12
            assert (transport.reads, transport.writes) == before
            assert outcomes(control.events)[-1] == ("cancelled", "set_current", "user_cancelled")
            await service.command(payload)
            assert len([e for e in outcomes(control.events) if e[0] == "cancelled"]) == 1
            service.stopping = True
            with pytest.raises(DeviceError, match="service_unavailable"):
                await service.command(payload)
        finally:
            await service.stop()

    asyncio.run(run())


def test_remote_cancels_over_http_without_refresh_or_false_availability(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        await service.control.async_set_charging_current(10)
        await service.control.async_set_charging_current(12)
        async with TestServer(create_app(service)) as server, ClientSession() as session:
            remote = ServiceClient(session, str(server.make_url("/")), TOKEN)
            await remote.async_connect()
            notifications = []
            refresh = AsyncMock(side_effect=AssertionError("cancel must not refresh"))
            control = ServiceControl(
                remote,                 state=lambda: service.data, request_refresh=refresh,
                notify_pending=lambda: notifications.append(control.pending_action),
            )
            service._success = False
            with pytest.raises(ServiceConnectionError, match="wallbox is unavailable"):
                await remote.update()
            assert remote.connected
            assert control.read_only  # Ordinary wallbox controls remain unavailable.
            assert control.can_cancel_pending
            before = (transport.reads[:], transport.writes[:])
            await control.async_cancel_pending_change()
            assert (transport.reads, transport.writes) == before
            assert control.pending_action is None
            assert not control.can_cancel_pending
            assert remote.snapshot["available"] is False
            assert notifications == [None]
            refresh.assert_not_awaited()
            await control.async_cancel_pending_change()
            assert notifications == [None]
        await service.stop()

    asyncio.run(run())


def test_remote_cancellation_rechecks_permission_and_connection(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        async with ClientSession() as session:
            remote = ServiceClient(session, "http://localhost", TOKEN)
            remote.apply_snapshot(service.snapshot())
            remote.request = AsyncMock(side_effect=AssertionError("refused cancel must not send"))
            with pytest.raises(DeviceError, match="read_only_mode"):
                await remote.execute("cancel_pending")
            await remote.async_close()
            with pytest.raises(DeviceError, match="service_unavailable"):
                await remote.execute("cancel_pending")
            remote.request.assert_not_awaited()
        await service.stop()

    asyncio.run(run())


def test_the_departure_plan_is_accepted_while_only_the_wallbox_is_away(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = ServiceClient(session, "http://localhost", TOKEN)
            snapshot = service.snapshot()
            snapshot["available"] = False
            remote.apply_snapshot(snapshot, require_available=False)
            sent = []

            async def request(method, path, payload=None):
                sent.append(payload["command"])
                return snapshot

            remote.request = request
            # Plan data needs no bus: the service takes all three kinds.
            for command in ("departure_trip", "departure_inputs", "departure_choice"):
                await remote.execute(command)
            assert sent == ["departure_trip", "departure_inputs", "departure_choice"]
            with pytest.raises(DeviceError, match=r"service_not_ready|service_unavailable"):
                await remote.execute("resume")
            await remote.async_close()
        await service.stop()

    asyncio.run(run())
