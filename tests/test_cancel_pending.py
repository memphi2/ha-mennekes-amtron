"""Cancelling a queue never resumes charging or requires a healthy Modbus bus."""

from __future__ import annotations

import asyncio
from copy import deepcopy
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
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from custom_components.mennekes_amtron_rtu.remote_errors import (
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
        assert control.supports_cancel_pending
        assert not control.can_cancel_pending
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(14)
        assert control.can_cancel_pending
        assert control.charging_setpoint == 14
        before = (harness.transport.reads[:], harness.transport.writes[:], harness.refreshes)
        await harness.client.async_close()

        await control.async_cancel_pending_change()
        assert not control.can_cancel_pending
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
        assert button.entity_category is None
        assert not button.available
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(12)
        entry.runtime_data.coordinator.last_update_success = False
        assert button.available
        control._options = replace(control.options, read_only=True)
        assert not button.available
        with pytest.raises(ServiceValidationError) as error:
            await button.async_press()
        assert error.value.translation_key == "read_only_mode"
        assert control.pending_action == "set_current"
        control._options = replace(control.options, read_only=False)
        writes = transport.writes[:]
        await button.async_press()
        assert transport.writes == writes
        assert not button.available
        await control.async_shutdown()

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
            assert snapshot["control"]["supports_cancel_pending"] is True
            assert snapshot["control"]["pending_action"] is None
            assert control.charging_setpoint == 12
            assert (transport.reads, transport.writes) == before
            assert outcomes(control.events)[-1] == ("cancelled", "set_current", "user_cancelled")
            await service.command(payload)
            assert len([e for e in outcomes(control.events) if e[0] == "cancelled"]) == 1
            service._stopping = True
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
            remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
            await remote.async_connect()
            notifications = []
            refresh = AsyncMock(side_effect=AssertionError("cancel must not refresh"))
            control = RemoteControl(
                remote, diagnostics=service.diagnostics, options=service.control.options,
                state=lambda: service.data, request_refresh=refresh,
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


@pytest.mark.parametrize("unsupported", ["missing", "disabled"])
def test_older_service_capabilities_disable_cancel_without_sending(tmp_path, unsupported):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = RemoteClient(session, "http://localhost", TOKEN)
            payload = service.snapshot()
            if unsupported == "missing":
                del payload["control"]["supports_cancel_pending"]
            else:
                payload["control"]["supports_cancel_pending"] = False
            remote.apply_snapshot(payload)
            remote.request = AsyncMock(side_effect=AssertionError("unsupported cancel must not send"))
            control = RemoteControl(
                remote, diagnostics=service.diagnostics, options=service.control.options,
                state=lambda: service.data, request_refresh=AsyncMock(),
            )
            assert not control.supports_cancel_pending
            assert not control.can_cancel_pending
            with pytest.raises(ServiceValidationError) as error:
                await control.async_cancel_pending_change()
            assert error.value.translation_key == "cancel_pending_not_supported"
            remote.request.assert_not_awaited()
        await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", []])
def test_service_cancel_capability_is_strict_boolean(tmp_path, invalid):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = RemoteClient(session, "http://localhost", TOKEN)
            payload = deepcopy(service.snapshot())
            payload["control"]["supports_cancel_pending"] = invalid
            with pytest.raises(ServiceConnectionError, match=r"control\.supports_cancel_pending"):
                remote.apply_snapshot(payload)
        await service.stop()

    asyncio.run(run())


def test_remote_cancellation_rechecks_permission_and_connection(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, read_only=True)
        async with ClientSession() as session:
            remote = RemoteClient(session, "http://localhost", TOKEN)
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
