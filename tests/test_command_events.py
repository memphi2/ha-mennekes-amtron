"""Command outcomes are sparse, bounded, deduplicated and physically honest."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.command_events import (
    MAX_EVENTS,
    CommandEvents,
)
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
    validation_error,
)
from custom_components.mennekes_amtron_rtu.event import async_setup_entry
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from custom_components.mennekes_amtron_rtu.remote_errors import ServiceConnectionError
from tests.fakes import raise_connection_error
from tests.ha_fakes import build_runtime
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_phase_pause_confirmation import _harness


def outcomes(feed):
    return [(row["outcome"], row["action"], row["reason"]) for row in feed.snapshot()["events"]]


def test_feed_is_bounded_no_replay_and_listener_failure_isolated(caplog):
    feed = CommandEvents()
    for _ in range(MAX_EVENTS + 5):
        feed.record("executed", "pause")
    seen = []
    unsubscribe = feed.listen(seen.append)
    assert seen == []
    assert len(feed.snapshot()["events"]) == MAX_EVENTS
    assert feed.snapshot()["events"][0]["sequence"] == 6

    def broken(_event):
        raise RuntimeError("private details")

    remove_broken = feed.listen(broken)
    feed.record("failed", "resume", "phase_unconfirmed")
    assert len(seen) == 1
    assert "private details" not in caplog.text
    remove_broken()
    unsubscribe()
    feed.record("executed", "pause")
    assert len(seen) == 1
    with pytest.raises(ValueError, match="invalid command event"):
        feed.record("secret", "pause")
    # Only recovery reports a recovery outcome.
    with pytest.raises(ValueError, match="invalid command event"):
        feed.record("recovery_failed", "pause")
    with pytest.raises(ValueError, match="invalid command event"):
        feed.record("executed", "recover")


def test_remote_feed_cursor_deduplicates_and_skips_offline_events():
    source, remote = CommandEvents(), CommandEvents(supported=False)
    seen = []
    remote.listen(seen.append)
    source.record("executed", "pause")
    remote.receive(CommandEvents.parse(source.snapshot()), connected=True)
    assert seen == []
    source.record("executed", "resume")
    remote.receive(CommandEvents.parse(source.snapshot()), connected=True)
    remote.receive(CommandEvents.parse(source.snapshot()), connected=True)
    assert [event.action for event in seen] == ["resume"]
    source.record("executed", "lock")
    remote.receive(CommandEvents.parse(source.snapshot()), connected=False)
    assert len(seen) == 1
    old = deepcopy(source.snapshot())
    source.record("executed", "unlock")
    remote.receive(CommandEvents.parse(source.snapshot()), connected=True)
    with pytest.raises(ValueError, match="backwards"):
        remote.receive(CommandEvents.parse(old), connected=True)
    restarted = CommandEvents()
    restarted.record("executed", "pause")
    remote.receive(CommandEvents.parse(restarted.snapshot()), connected=True)
    assert len(seen) == 2
    remote.receive(CommandEvents.parse(None), connected=True)
    assert remote.supported is False


@pytest.mark.parametrize("mutate", [
    lambda _: [],
    lambda raw: {**raw, "boot_id": "z" * 32},
    lambda raw: {**raw, "sequence": True},
    lambda raw: {**raw, "events": [{}] * (MAX_EVENTS + 1)},
    lambda raw: {**raw, "events": [1]},
    lambda raw: {**raw, "events": [{"sequence": 1}]},
    lambda raw: {**raw, "events": [{**raw["events"][0], "sequence": 0}]},
    lambda raw: {**raw, "events": [{**raw["events"][0], "action": "raw secret"}]},
    lambda raw: {**raw, "events": [{**raw["events"][0], "outcome": "raw secret"}]},
    lambda raw: {**raw, "events": [{**raw["events"][0], "reason": "raw secret"}]},
    lambda raw: {**raw, "events": []},
    lambda raw: {**raw, "sequence": 2},
])
def test_remote_event_metadata_is_strictly_validated(mutate):
    feed = CommandEvents()
    feed.record("executed", "pause")
    with pytest.raises(ValueError, match="command event"):
        CommandEvents.parse(mutate(feed.snapshot()))


def test_empty_feed_parses():
    assert CommandEvents.parse(CommandEvents().snapshot())[1:] == (0, [])


def test_commands_emit_only_terminal_outcomes_and_replacement():
    async def run():
        harness = Harness()
        control = await harness.connect()
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(12)
        await control.async_set_charging_current(14)
        assert outcomes(control.events) == [
            ("executed", "set_current", "completed"),
            ("replaced", "set_current", "replaced"),
        ]
        await control.async_shutdown()
        assert outcomes(control.events)[-1] == ("cancelled", "set_current", "shutdown")
    asyncio.run(run())


@pytest.mark.parametrize("rejected", [False, True])
def test_unacknowledged_write_is_uncertain_but_protocol_rejection_is_failed(rejected):
    async def run():
        harness = Harness()
        control = await harness.connect()
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = (
            2 if rejected else raise_connection_error()
        )
        with pytest.raises(HomeAssistantError):
            await control.async_pause_charging()
        assert outcomes(control.events) == [(
            "failed" if rejected else "uncertain", "pause",
            "write_rejected" if rejected else "connection_error",
        )]
    asyncio.run(run())


def test_failed_refresh_after_write_does_not_claim_the_command_failed():
    async def run():
        harness = Harness()
        control = await harness.connect()
        control._request_refresh = AsyncMock(side_effect=RuntimeError("private failure"))
        with pytest.raises(RuntimeError, match="private failure"):
            await control.async_pause_charging()
        assert outcomes(control.events) == [("executed", "pause", "status_refresh_failed")]
    asyncio.run(run())


@pytest.mark.parametrize("permission", [True, False])
def test_deferred_preflight_failure_emits_failed_once(permission):
    async def run():
        harness = Harness()
        control = await harness.connect()
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(12)

        def no_permission():
            if not permission:
                raise DeviceError("service_unavailable")
            raise validation_error("read_only_mode")

        control._validate_deferred = no_permission
        await asyncio.wait_for(control._flush_task, 1)
        assert outcomes(control.events)[-1] == (
            "failed", "set_current", "permission_denied" if permission else "connection_error",
        )
        assert len(outcomes(control.events)) == 2
    asyncio.run(run())


def test_phase_abort_reports_reason_without_claiming_phase_execution():
    async def run():
        harness = _harness()
        control = await harness.connect()
        from custom_components.mennekes_amtron_rtu.device.enums import RequestedPhases
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await asyncio.wait_for(control._flush_task, 1)
        assert outcomes(control.events) == [("failed", "single_phase", "phase_timeout")]
    asyncio.run(run())


def test_recovery_success_requires_fresh_zero_and_failure_a_confirmation_window():
    async def run():
        harness = Harness(error_code=200)
        control = await harness.connect()
        control.observe_recovery(0, harness.client.write_state.current_write_generation)
        assert await control.async_recover_from_error() is True
        assert outcomes(control.events) == []
        control.observe_recovery(None, harness.client.write_state.current_write_generation)
        control.observe_recovery(200, harness.client.write_state.current_write_generation)
        assert outcomes(control.events) == []
        control.observe_recovery(0, harness.client.write_state.current_write_generation)
        control.observe_recovery(0, harness.client.write_state.current_write_generation)
        assert outcomes(control.events) == [("recovery_succeeded", "recover", "completed")]
        harness.clock[0] += 10
        assert await control.async_recover_from_error() is True
        harness.clock[0] += 30
        control.observe_recovery(200, harness.client.write_state.current_write_generation)
        assert outcomes(control.events)[-1] == ("recovery_failed", "recover", "error_still_active")
    asyncio.run(run())


def test_service_event_extension_is_optional_and_identity_validated_before_delivery(tmp_path):
    async def run():
        service, _transport, clock = await service_fixture(tmp_path)
        async with ClientSession() as session:
            remote = RemoteClient(session, "http://localhost", TOKEN)
            remote.apply_snapshot(service.snapshot())
            events = []
            remote.events.listen(events.append)
            await service.control.async_pause_charging()
            snapshot = service.snapshot()
            remote.apply_snapshot(snapshot)
            remote.apply_snapshot(snapshot)
            assert [event.action for event in events] == ["pause"]
            wrapper = RemoteControl(
                remote, diagnostics=service.diagnostics, options=service.control.options,
                state=lambda: service.data, request_refresh=AsyncMock(),
            )
            assert wrapper.events is remote.events
            old = deepcopy(snapshot)
            del old["command_events"]
            remote.apply_snapshot(old)
            assert remote.events.supported is False
            invalid = deepcopy(snapshot)
            invalid["command_events"] = []
            with pytest.raises(ServiceConnectionError, match="invalid service snapshot \\(command_events\\)"):
                remote.apply_snapshot(invalid)
            remote.apply_snapshot(snapshot)
            clock[0] += 301
            await service.control.async_set_lock_evse(True)
            remote.apply_snapshot(service.snapshot())
            with pytest.raises(ServiceConnectionError, match="invalid service snapshot \\(command_events\\)"):
                remote.apply_snapshot(snapshot)
            assert not remote.connected
        await service.stop()
    asyncio.run(run())


def test_event_entity_subscribes_without_replaying_and_tracks_feed_support(monkeypatch):
    async def run():
        hass, entry, _transport = await build_runtime()
        feed = entry.runtime_data.control.events
        feed.record("executed", "pause")
        added = []
        await async_setup_entry(hass, entry, added.extend)
        entity = added[0]
        assert entity.entity_registry_enabled_default is False
        assert entity.available
        assert entity.unique_id.endswith("_control_result")
        writes, removals = [], []
        monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
        monkeypatch.setattr(entity, "async_write_ha_state", lambda: writes.append(True))
        monkeypatch.setattr(entity, "async_on_remove", removals.append)
        await entity.async_added_to_hass()
        assert writes == []
        feed.record("executed", "resume")
        # The type names the command, so HA's history shows which one it was.
        assert entity.state_attributes["event_type"] == "resume_executed"
        assert entity.state_attributes["action"] == "resume"
        assert entity.state_attributes["result"] == "executed"
        feed.record("recovery_failed", "recover", "error_still_active")
        assert entity.state_attributes["event_type"] == "recovery_failed"
        feed.record("uncertain", "recover", "unknown")
        assert entity.state_attributes["event_type"] == "recovery_uncertain"
        assert len(writes) == 3
        removals[0]()
        feed.record("executed", "lock")
        assert len(writes) == 3
        feed.supported = False
        assert not entity.available
    asyncio.run(run())


def test_idempotent_phase_command_reports_replaced_pending_pause():
    async def run():
        harness = Harness()
        control = await harness.connect()
        from custom_components.mennekes_amtron_rtu.device.enums import RequestedPhases
        await control.async_set_charging_current(12)
        await control.async_pause_charging()
        await control.async_set_requested_phases(RequestedPhases.ALL_AVAILABLE)
        assert outcomes(control.events)[-2:] == [
            ("replaced", "pause", "replaced"), ("executed", "all_phases", "completed"),
        ]
        await control.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("rejected", [False, True])
def test_recovery_write_failure_reports_uncertainty_or_rejection(rejected):
    async def run():
        harness = Harness(error_code=200)
        control = await harness.connect()
        harness.transport.write_exceptions[R.HEARTBEAT.address] = (
            2 if rejected else raise_connection_error()
        )
        with pytest.raises(HomeAssistantError):
            await control.async_recover_from_error()
        assert outcomes(control.events) == [(
            "recovery_failed" if rejected else "uncertain", "recover",
            "write_rejected" if rejected else "connection_error",
        )]
        control.observe_recovery(0, harness.client.write_state.current_write_generation)
        assert len(outcomes(control.events)) == 1
    asyncio.run(run())


def test_recovery_cannot_be_confirmed_by_a_poll_started_before_the_recovery(monkeypatch):
    async def run():
        from custom_components.mennekes_amtron_rtu.coordinator import (
            MennekesAmtronCoordinator,
        )
        from custom_components.mennekes_amtron_rtu.device.data import ConnectionState
        from tests.ha_fakes import FakeConfigEntry, FakeHass

        harness = Harness(error_code=0)
        control = await harness.connect()
        coordinator = MennekesAmtronCoordinator(
            FakeHass(), FakeConfigEntry(), client=harness.client,
            identity=harness.identity, connection_state=ConnectionState(), scan_interval=5,
        )
        coordinator.observe_recovery = control.observe_recovery
        error_read, release_poll = asyncio.Event(), asyncio.Event()
        read_block = harness.client.async_read_block

        async def delayed_block(block):
            result = await read_block(block)
            if "error_code" in result and not error_read.is_set():
                error_read.set()
                await release_poll.wait()
            return result

        monkeypatch.setattr(harness.client, "async_read_block", delayed_block)
        in_flight = asyncio.create_task(coordinator._async_update_data())
        await asyncio.wait_for(error_read.wait(), 1)
        harness.transport.bank[R.ERROR_CODE.address] = 200
        assert await control.async_recover_from_error() is True
        release_poll.set()
        coordinator.data = await asyncio.wait_for(in_flight, 1)
        assert coordinator.data.get("error_code") == 0
        assert coordinator.data.current_write_generation < harness.client.write_state.current_write_generation
        assert outcomes(control.events) == []
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.get("error_code") == 200
        assert outcomes(control.events) == []
        harness.transport.bank[R.ERROR_CODE.address] = 0
        await coordinator._async_update_data()
        assert outcomes(control.events) == [("recovery_succeeded", "recover", "completed")]
    asyncio.run(run())


def test_service_recovery_observation_uses_the_poll_generation(tmp_path, monkeypatch):
    async def run():
        from custom_components.mennekes_amtron_rtu.device.data import WallboxData

        service, transport, _clock = await service_fixture(tmp_path)
        transport.bank[R.ERROR_CODE.address] = 200
        await service.control.async_recover_from_error()
        generation = service.client.write_state.current_write_generation
        snapshot = WallboxData(values={"error_code": 0}, current_write_generation=generation - 1)
        monkeypatch.setattr(service._poller, "async_poll", AsyncMock(return_value=snapshot))
        await service.refresh()
        assert outcomes(service.control.events) == []
        snapshot.current_write_generation = generation
        await service.refresh()
        assert outcomes(service.control.events) == [("recovery_succeeded", "recover", "completed")]
        await service.stop()
    asyncio.run(run())
