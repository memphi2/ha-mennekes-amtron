"""A direct entry's service: started once, kept through reloads, stopped on unload."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from custom_components.mennekes_amtron_rtu import embedded, service_transport
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronIdentityError,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_api import ServiceBusyError
from custom_components.mennekes_amtron_rtu.device.waveshare_auth import (
    WaveshareAuthenticationRejectedError,
)
from custom_components.mennekes_amtron_rtu.service_transport import EmbeddedTransport
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass, capture_timers
from tests.test_init import _patch_client
from tests.test_modbus_service import current_writes

MASTER = {CONF_CONTROL_MODE: CONTROL_MODE_MASTER}


def _hosts(hass):
    return hass.data.get(f"{DOMAIN}_embedded", {})


def _setup(monkeypatch, tmp_path, *, options=None, bank=None):
    hass = FakeHass(str(tmp_path))
    entry = FakeConfigEntry(options=dict(MASTER if options is None else options))
    wallbox = FakeModbusClient(device_bank() if bank is None else bank)
    _patch_client(monkeypatch, wallbox)
    return hass, entry, wallbox


def test_a_started_service_is_reused_and_takes_changed_intervals(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        try:
            assert _hosts(hass) == {entry.entry_id: host}
            assert host._service.heartbeat.running
            entry.options = {
                **MASTER, CONF_SCAN_INTERVAL_SECONDS: 30,
                CONF_MEASUREMENT_INTERVAL_SECONDS: 60, CONF_CURRENT_LIMIT: 10,
            }
            again = await embedded.async_acquire(hass, entry)
            # The same connection and heartbeat carry on with the new limits.
            assert again is host
            assert host.config.scan_interval == 30
            assert host.config.measurement_interval == 60
            assert host._service.config.current_cap == 10
            assert host._service.heartbeat.running
            assert wallbox.close_calls == 0
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)
        assert wallbox.close_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["connection", "read_only"])
def test_a_changed_connection_or_permission_starts_a_new_service(monkeypatch, tmp_path, change):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        old = await embedded.async_acquire(hass, entry)
        if change == "connection":
            entry.data = {**entry.data, CONF_PORT: "/dev/other"}
        else:
            entry.options = {}
        new = await embedded.async_acquire(hass, entry)
        try:
            assert new is not old
            assert _hosts(hass) == {entry.entry_id: new}
            assert not old._service.heartbeat.running
            assert new._service.heartbeat.running is (change == "connection")
            assert new.config.read_only is (change == "read_only")
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_settings_the_running_service_refuses_are_applied_by_a_restart(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        old = await embedded.async_acquire(hass, entry)

        async def refuse(_config):
            raise DeviceError("service_unavailable")

        monkeypatch.setattr(old._service, "apply_runtime_settings", refuse)
        entry.options = {**MASTER, CONF_SCAN_INTERVAL_SECONDS: 30}
        new = await embedded.async_acquire(hass, entry)
        try:
            assert new is not old
            assert new.config.scan_interval == 30
            assert not old._service.heartbeat.running
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_failed_start_leaves_nothing_running(monkeypatch, tmp_path):
    async def run():
        wallbox = FakeModbusClient()
        wallbox.connect_result = False
        hass, entry, _ = _setup(monkeypatch, tmp_path)
        _patch_client(monkeypatch, wallbox)
        with pytest.raises(AmtronConnectionError):
            await embedded.async_acquire(hass, entry)
        assert _hosts(hass) == {}
        assert not wallbox.connected

    asyncio.run(run())


def test_another_wallbox_is_rejected_before_any_write(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path, bank=device_bank(serial="OTHER0000000"))
        with pytest.raises(AmtronIdentityError):
            await embedded.async_acquire(hass, entry, "ABC123456789")
        assert wallbox.writes == []
        assert _hosts(hass) == {}

    asyncio.run(run())


def test_a_start_that_never_answers_times_out_and_stops(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        entered = asyncio.Event()

        async def blocked(_client):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(service_runtime, "async_read_identity", blocked)
        monkeypatch.setattr(embedded, "_START_SECONDS", 0.05)
        with pytest.raises(TimeoutError):
            await embedded.async_acquire(hass, entry)
        assert entered.is_set()
        assert _hosts(hass) == {}
        assert wallbox.close_calls == 1

    asyncio.run(run())


def test_a_reload_keeps_the_service_and_every_other_release_stops_it(monkeypatch, tmp_path):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        embedded.mark_reloading(hass, entry.entry_id, True)
        await embedded.async_release(hass, entry.entry_id)
        assert _hosts(hass) == {entry.entry_id: host}
        assert host._service.heartbeat.running
        # A failed setup during the reload must not keep it.
        await embedded.async_release(hass, entry.entry_id, force=True)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        embedded.mark_reloading(hass, entry.entry_id, False)

        host = await embedded.async_acquire(hass, entry)
        await embedded.async_release(hass, entry.entry_id)
        assert _hosts(hass) == {}
        assert wallbox.close_calls == 2
        # Releasing an entry without a service is harmless.
        await embedded.async_release(hass, entry.entry_id)

    asyncio.run(run())


def test_forgetting_a_removed_entry_stops_it_and_deletes_what_it_saved(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        directory = embedded.state_directory(hass, entry.entry_id)
        assert (directory / "intent.json").exists()
        embedded.mark_reloading(hass, entry.entry_id, True)
        await embedded.async_forget(hass, entry.entry_id)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        assert not directory.exists()
        assert entry.entry_id not in hass.data[f"{DOMAIN}_reloading"]
        # Forgetting twice, or an entry that never ran, is harmless.
        await embedded.async_forget(hass, "never-loaded")

    asyncio.run(run())


class _Api:
    def __init__(self, reply=None):
        self.reply = reply
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = []

    async def dispatch(self, method, path, payload):
        self.calls.append((method, path, payload))
        self.started.set()
        await self.release.wait()
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def test_an_abandoned_request_keeps_running_until_the_service_drains(monkeypatch):
    async def run():
        api = _Api((200, {"accepted": True}))
        transport = EmbeddedTransport(api)
        monkeypatch.setattr(service_transport, "_TIMEOUT_SECONDS", 0.02)
        with pytest.raises(TimeoutError):
            await transport.call("POST", "/v2/command", {"command": "pause"})
        # The command was not cancelled between its writes.
        assert len(transport._running) == 1
        api.release.set()
        await transport.async_drain()
        assert not transport._running
        # Nothing left to wait for.
        await transport.async_drain()

    asyncio.run(run())


def test_a_failed_abandoned_request_is_not_reported_twice(monkeypatch):
    async def run():
        api = _Api(DeviceError("service_unavailable"))
        transport = EmbeddedTransport(api)
        monkeypatch.setattr(service_transport, "_TIMEOUT_SECONDS", 0.02)
        with pytest.raises(TimeoutError):
            await transport.call("GET", "/v2/state", None)
        api.release.set()
        await transport.async_drain()
        assert not transport._running

    asyncio.run(run())


def test_a_busy_service_answers_like_its_http_api():
    async def run():
        api = _Api(ServiceBusyError())
        api.release.set()
        transport = EmbeddedTransport(api)
        assert await transport.call("POST", "/v2/command", {"command": "pause"}) == (
            503, {"error": "service_unavailable"},
        )

    asyncio.run(run())


def test_requests_and_replies_cross_as_independent_json():
    async def run():
        reply = {"values": {"a": 1}}
        api = _Api((200, reply))
        api.release.set()
        transport = EmbeddedTransport(api)
        payload = {"command": "set_current", "value": 8}
        status, body = await transport.call("POST", "/v2/command", payload)
        assert (status, body) == (200, reply)
        assert body is not reply
        assert api.calls[0][2] == payload
        assert api.calls[0][2] is not payload

    asyncio.run(run())


def test_stopping_home_assistant_stops_every_service(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        assert host._service.heartbeat.running
        # Home Assistant stops without unloading its entries.
        await hass.bus.fire(EVENT_HOMEASSISTANT_STOP)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        assert (tmp_path / ".storage" / f"{DOMAIN}.{entry.entry_id}" / "intent.json").exists()

    asyncio.run(run())


def test_a_rejected_gateway_password_is_told_apart_from_a_lost_wallbox(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        try:
            assert host.credentials_rejected() is False
            host._service.failure = AmtronConnectionError("lost")
            assert host.credentials_rejected() is False
            host._service.failure = WaveshareAuthenticationRejectedError("rejected")
            assert host.credentials_rejected() is True
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_any_other_failure_while_applying_settings_still_stops_the_service(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        old = await embedded.async_acquire(hass, entry)

        async def broken(_config):
            raise ValueError("unexpected")

        monkeypatch.setattr(old._service, "apply_runtime_settings", broken)
        with pytest.raises(ValueError, match="unexpected"):
            await embedded.async_acquire(hass, entry)
        # Nothing keeps the port and the heartbeat behind the failed setup.
        assert _hosts(hass) == {}
        assert not old._service.heartbeat.running

    asyncio.run(run())


def test_settings_that_wait_too_long_are_applied_by_a_restart(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        old = await embedded.async_acquire(hass, entry)
        monkeypatch.setattr(embedded, "_APPLY_SECONDS", 0.01)

        async def hang(_config):
            await asyncio.sleep(3600)

        monkeypatch.setattr(old._service, "apply_runtime_settings", hang)
        new = await embedded.async_acquire(hass, entry)
        try:
            assert new is not old
            assert not old._service.heartbeat.running
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_service_whose_heartbeat_dies_is_stopped_and_the_entry_reloaded(
    monkeypatch, tmp_path, caplog,
):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        reloads = []
        hass.config_entries.async_schedule_reload = reloads.append
        tasks = []
        create_task = hass.async_create_task

        def record(target, name=None, **kwargs):
            task = create_task(target, name, **kwargs)
            tasks.append(task)
            return task

        hass.async_create_task = record
        host = await embedded.async_acquire(hass, entry)
        service_task = host._service._task
        # The service ends itself rather than run without its heartbeat.
        host._service._heartbeat_task.cancel()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if reloads:
                break
        assert reloads == [entry.entry_id]
        assert _hosts(hass) == {}
        assert service_task.done()
        assert service_task.exception() is not None
        assert not host._service.heartbeat.running
        assert wallbox.close_calls == 1
        assert "Heartbeat: task stopped unexpectedly (cancelled)" in caplog.text
        assert "Service: stopped unexpectedly (" in caplog.text
        # The restart itself finished without an error.
        assert len(tasks) == 1
        await tasks[0]
        # Its end was reported once; nothing that stops it later fails on it.
        await host.async_stop()
        await embedded.async_release(hass, entry.entry_id, force=True)
        await hass.bus.fire(EVENT_HOMEASSISTANT_STOP)
        # A stop the owner asked for is not an unexpected end.
        host._ended(service_task)
        await asyncio.sleep(0)
        assert reloads == [entry.entry_id]

    asyncio.run(run())


def test_a_service_stopped_by_its_owner_is_not_reported_as_ended(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        reloads = []
        hass.config_entries.async_schedule_reload = reloads.append
        host = await embedded.async_acquire(hass, entry)
        service = host._service

        async def broken_stop():
            raise RuntimeError("save failed")

        monkeypatch.setattr(service, "stop", broken_stop)
        # A failure while stopping a healthy service still reaches its owner.
        with pytest.raises(RuntimeError, match="save failed"):
            await embedded.async_release(hass, entry.entry_id, force=True)
        monkeypatch.undo()
        await service.stop()
        assert reloads == []

    asyncio.run(run())


def test_a_command_in_progress_finishes_its_writes_before_the_connection_closes(
    monkeypatch, tmp_path,
):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        service = host._service
        events = []
        between = asyncio.Event()
        gate = asyncio.Event()
        close = wallbox.close

        async def two_writes(_payload):
            # A command with two writes whose caller gives up between them.
            await service.client.async_write_register(R.CHARGING_RELEASE, 0)
            events.append("first write")
            between.set()
            await gate.wait()
            await service.client.async_write_register(R.CHARGING_RELEASE, 1)
            events.append("second write")
            return service.snapshot()

        def record_close():
            events.append("close")
            close()

        monkeypatch.setattr(service, "command", two_writes)
        monkeypatch.setattr(wallbox, "close", record_close)
        monkeypatch.setattr(service_transport, "_TIMEOUT_SECONDS", 0.05)
        with pytest.raises(TimeoutError):
            await host.transport.call("POST", "/v2/command", {"command": "pause"})
        assert between.is_set()
        release = asyncio.create_task(embedded.async_release(hass, entry.entry_id, force=True))
        for _ in range(10):
            await asyncio.sleep(0)
        # The service is not stopped under the command.
        assert events == ["first write"]
        assert not release.done()
        assert service.heartbeat.running
        gate.set()
        await release
        assert events == ["first write", "second write", "close"]
        assert wallbox.bank[R.CHARGING_RELEASE.address] == 1
        assert not service.heartbeat.running

    asyncio.run(run())


@pytest.mark.parametrize("stopped_service_refuses", [False, True])
def test_a_shutdown_while_settings_are_applied_never_returns_a_stopped_service(
    monkeypatch, tmp_path, stopped_service_refuses,
):
    async def run():
        hass, entry, wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        entered = asyncio.Event()
        gate = asyncio.Event()
        apply = host._service.apply_runtime_settings

        async def slow_apply(config):
            entered.set()
            await gate.wait()
            if stopped_service_refuses:
                # The stopped service refuses the settings; that must not
                # start a new service behind the shutdown.
                await apply(config)
            # Otherwise accepted just before the shutdown reached the service.

        monkeypatch.setattr(host._service, "apply_runtime_settings", slow_apply)
        entry.options = {**MASTER, CONF_SCAN_INTERVAL_SECONDS: 30}
        acquiring = asyncio.create_task(embedded.async_acquire(hass, entry))
        await entered.wait()
        # The running service is still registered, so the shutdown finds it.
        assert _hosts(hass) == {entry.entry_id: host}
        await hass.bus.fire(EVENT_HOMEASSISTANT_STOP)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        assert wallbox.close_calls == 1
        gate.set()
        with pytest.raises(DeviceError) as err:
            await acquiring
        assert err.value.translation_key == "service_unavailable"
        # Nothing was started behind the shutdown.
        assert _hosts(hass) == {}
        assert wallbox.close_calls == 1
        assert not wallbox.connected
        assert not host._service.heartbeat.running

    asyncio.run(run())


def test_a_replaced_service_ending_late_does_not_reload_the_entry(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        reloads = []
        hass.config_entries.async_schedule_reload = reloads.append
        host = await embedded.async_acquire(hass, entry)
        ended = host.on_ended
        await embedded.async_release(hass, entry.entry_id, force=True)
        ended()
        await asyncio.sleep(0)
        assert reloads == []

    asyncio.run(run())


@pytest.mark.parametrize(("options", "expected"), [(MASTER, 6.0), ({**MASTER, CONF_CURRENT_LIMIT: 10}, 10.0)])
def test_without_a_limit_from_the_user_or_the_wallbox_only_the_minimum_is_allowed(
    monkeypatch, tmp_path, options, expected,
):
    async def run():
        # A wallbox that does not report its maximum current.
        hass, entry, _wallbox = _setup(
            monkeypatch, tmp_path, options=options, bank=device_bank(max_current=0),
        )
        host = await embedded.async_acquire(hass, entry)
        try:
            assert host._service.identity.max_evse_current is None
            assert host._service.control.max_charging_current == expected
            # A later settings change keeps the same bound.
            entry.options = {**options, CONF_SCAN_INTERVAL_SECONDS: 30}
            assert await embedded.async_acquire(hass, entry) is host
            assert host._service.control.max_charging_current == expected
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_running_charge_at_the_wallbox_maximum_is_not_lowered_at_start(monkeypatch, tmp_path):
    async def run():
        bank = device_bank(max_current=16, evse_state=EvseState.CHARGING, charging_current=16)
        bank[R.CP_STATE.address] = CpState.C2
        hass, entry, wallbox = _setup(monkeypatch, tmp_path, bank=bank)
        host = await embedded.async_acquire(hass, entry)
        try:
            # Without a limit of its own the entry follows the wallbox.
            assert host._service.config.current_cap == 16
            assert host._service.control.max_charging_current == 16
            await asyncio.sleep(0.2)
            entry.options = {**MASTER, CONF_SCAN_INTERVAL_SECONDS: 30}
            assert await embedded.async_acquire(hass, entry) is host
            assert host._service.config.current_cap == 16
            assert host._service.config.cap_from_wallbox is True
            assert host._service.control.max_charging_current == 16
            assert all(value >= 16 for value in current_writes(wallbox))
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_limit_of_the_user_above_the_wallbox_maximum_allows_only_the_wallbox_maximum(
    monkeypatch, tmp_path,
):
    async def run():
        hass, entry, _wallbox = _setup(
            monkeypatch, tmp_path, options={**MASTER, CONF_CURRENT_LIMIT: 32},
        )
        host = await embedded.async_acquire(hass, entry)
        try:
            assert host._service.config.cap_from_wallbox is False
            assert host._service.config.current_cap == 32
            assert host._service.control.max_charging_current == 16
        finally:
            await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())


def test_a_failed_last_save_does_not_fail_the_stop(monkeypatch, tmp_path, caplog):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        service = host._service

        async def full_disk():
            raise OSError("no space left")

        monkeypatch.setattr(service, "stop", full_disk)
        await embedded.async_release(hass, entry.entry_id, force=True)
        assert _hosts(hass) == {}
        assert "Service: state not saved while stopping (OSError)" in caplog.text
        monkeypatch.undo()
        await service.stop()

    asyncio.run(run())


def test_one_service_failing_to_stop_does_not_keep_the_others_running(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        stopped = []

        class Broken:
            async def async_stop(self):
                stopped.append("broken")
                raise RuntimeError("stop failed")

        # Stopped in reverse order: the broken one first.
        _hosts(hass)["other"] = Broken()
        await hass.bus.fire(EVENT_HOMEASSISTANT_STOP)
        assert stopped == ["broken"]
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running

    asyncio.run(run())


def test_a_draining_transport_refuses_new_requests():
    async def run():
        api = _Api((200, {"accepted": True}))
        transport = EmbeddedTransport(api)
        running = asyncio.create_task(transport.call("POST", "/v2/command", {"command": "pause"}))
        await api.started.wait()
        draining = asyncio.create_task(transport.async_drain())
        await asyncio.sleep(0)
        # Like a web server that no longer accepts connections.
        assert await transport.call("GET", "/v2/state", None) == (503, {"error": "service_unavailable"})
        assert len(api.calls) == 1
        api.release.set()
        await draining
        assert await running == (200, {"accepted": True})
        assert await transport.call("GET", "/v2/state", None) == (503, {"error": "service_unavailable"})

    asyncio.run(run())


def test_a_half_initialized_service_starts_over_and_then_starts_its_heartbeat(
    monkeypatch, tmp_path,
):
    async def run():
        _hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = embedded.EmbeddedService(embedded.embedded_config(entry), tmp_path)
        service = host._service
        failures = [OSError("state unreadable")]

        async def dependents():
            if failures:
                raise failures.pop()

        monkeypatch.setattr(service, "_initialize_dependents", dependents)
        with pytest.raises(OSError, match="state unreadable"):
            await service._initialize()
        # Nothing is left that a later poll would mistake for a started service.
        assert (service.control, service.goal, service.soc_goal) == (None, None, None)
        assert not service.heartbeat.running
        await service._initialize()
        assert service.control is not None
        assert service.heartbeat.running
        await service.stop()

    asyncio.run(run())


def test_a_setup_that_will_be_retried_keeps_the_service_until_the_retry_stays_away(
    monkeypatch, tmp_path,
):
    async def run():
        timers = capture_timers(monkeypatch, embedded)
        hass, entry, _wallbox = _setup(monkeypatch, tmp_path)
        host = await embedded.async_acquire(hass, entry)
        embedded.async_release_unless_retried(hass, entry.entry_id)
        assert host._service.heartbeat.running
        # The retry takes the running service over and nothing stops it later.
        assert await embedded.async_acquire(hass, entry) is host
        assert [timer[2] for timer in timers] == [False]
        # Asked twice, only the newest wait counts.
        embedded.async_release_unless_retried(hass, entry.entry_id)
        embedded.async_release_unless_retried(hass, entry.entry_id)
        assert [timer[2] for timer in timers] == [False, False, True]
        await timers[-1][1](None)
        assert _hosts(hass) == {}
        assert not host._service.heartbeat.running
        # A release of its own ends the wait as well.
        host = await embedded.async_acquire(hass, entry)
        embedded.async_release_unless_retried(hass, entry.entry_id)
        await embedded.async_release(hass, entry.entry_id, force=True)
        assert not timers[-1][2]
        assert not host._service.heartbeat.running

    asyncio.run(run())


def test_every_poll_is_reported_to_one_listener_until_it_is_removed(monkeypatch, tmp_path):
    async def run():
        hass, entry, _wallbox = _setup(
            monkeypatch, tmp_path, options={**MASTER, "scan_interval_seconds": 0.01},
        )
        host = await embedded.async_acquire(hass, entry)
        service = host._service
        polls = []
        remove = host.async_on_polled(lambda: polls.append("first"))
        # A newer listener replaces it; removing the old one leaves the new.
        host.async_on_polled(lambda: polls.append("second"))
        remove()
        polled = asyncio.Event()
        original = service.on_polled

        def notify():
            original()
            polled.set()

        service.on_polled = notify
        await asyncio.wait_for(polled.wait(), 10)
        assert set(polls) == {"second"}
        await embedded.async_release(hass, entry.entry_id, force=True)

    asyncio.run(run())
