"""Cost calculations require real meter samples, never repeatedly polled cache."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession
from homeassistant.helpers.update_coordinator import UpdateFailed

import custom_components.mennekes_amtron_rtu.coordinator as coordinator_module
from custom_components.mennekes_amtron_rtu.coordinator import ServiceCoordinator
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.meter_log import ChargeMeter
from custom_components.mennekes_amtron_rtu.device.service_api import ServiceApi
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from custom_components.mennekes_amtron_rtu.service_transport import EmbeddedTransport
from tests.fakes import FakeModbusClient, device_bank, put
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_modbus_service import TOKEN, service_fixture


async def owner(tmp_path, *, layout=R.LAYOUT_V01_03, state=EvseState.IDLE, read_only=True):
    bank = device_bank(layout=layout, evse_state=state)
    put(bank, R.CP_STATE, CpState.A1 if state is EvseState.IDLE else CpState.C2)
    put(bank, R.ENERGY_TOTAL, 100)
    transport = FakeModbusClient(bank)
    client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
    service = ModbusService(
        ServerConfig(client.config, TOKEN, read_only=read_only), tmp_path / "intent.json", client=client,
    )
    await service._initialize()
    return service, transport


async def observe(service, transport, clock, at, state, energy):
    clock[0] = at
    put(transport.bank, R.EVSE_STATE, state)
    put(transport.bank, R.CP_STATE, CpState.A1 if state is EvseState.IDLE else CpState.C2)
    put(transport.bank, R.ENERGY_TOTAL, energy)
    await service.refresh()


def reading(state, total):
    cp = CpState.A1 if state == EvseState.IDLE else CpState.C2 if state == EvseState.CHARGING else CpState.B1
    return WallboxData(
        values={"evse_state": state, "cp_state": cp, "energy_total": total}, energy_read=True,
    )


def test_service_energy_sample_retains_actual_attempt_time_through_cached_polls(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            try:
                initial = {"observed_at": 1000.0, "value": 100.0}
                assert service.snapshot()["energy_sample"] == initial
                assert service.data.energy_read
                assert service.data.energy_observed_at == 1000
                await observe(service, transport, clock, 1001, EvseState.IDLE, 101)
                assert service.snapshot()["energy_sample"] == initial
                assert not service.data.energy_read
                assert service.data.energy_observed_at == 1000
                transport.reads.clear()
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 102)
                assert service.snapshot()["energy_sample"] == {"observed_at": 1010, "value": 102}
                assert transport.reads.count((R.ENERGY_TOTAL.address, 4)) == 1
                assert service.data.energy_read
                assert service.data.energy_observed_at == 1010
                snapshot = service.snapshot()
                snapshot["energy_sample"]["value"] = 9000
                assert service.snapshot()["energy_sample"]["value"] == 102
            finally:
                await service.stop()

    asyncio.run(run())


def test_service_failed_meter_is_a_new_unknown_sample_and_poll_failure_clears_it(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            try:
                transport.read_exceptions[R.ENERGY_TOTAL.address] = 4
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 102)
                assert service.snapshot()["energy_sample"] == {"observed_at": 1010, "value": None}
                assert service.data.energy_read
                service._poller.async_poll = AsyncMock(side_effect=AmtronConnectionError("offline"))
                with pytest.raises(AmtronConnectionError):
                    await service.refresh()
                assert service.snapshot()["energy_sample"] is None
                assert service.data.energy_observed_at is None
                assert not service.snapshot()["available"]
            finally:
                await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, -1, True, "100", float("nan")])
def test_invalid_device_meter_value_is_unknown_in_optional_sample(tmp_path, value):
    async def run():
        service, _transport = await owner(tmp_path)
        try:
            service._poller.async_poll = AsyncMock(return_value=reading(EvseState.CHARGING, value))
            await service.refresh()
            assert service.snapshot()["energy_sample"]["value"] is None
        finally:
            await service.stop()

    asyncio.run(run())


def test_unsupported_layout_has_no_energy_sample(tmp_path):
    async def run():
        service, _transport = await owner(tmp_path, layout=R.LAYOUT_V01_00)
        try:
            assert service.snapshot()["energy_sample"] is None
            assert service.data.energy_observed_at is None
        finally:
            await service.stop()

    asyncio.run(run())


def test_remote_repeated_cache_is_not_fresh_and_command_does_not_consume_next_sample(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                payload["energy_sample"] = {"observed_at": 1000.0, "value": 100.0}
                first = client.apply_snapshot(payload)
                assert first.energy_read
                assert first.energy_observed_at == 1000
                assert first.get("energy_total") == 100
                repeated = client.apply_snapshot(deepcopy(payload))
                assert not repeated.energy_read
                assert repeated.energy_observed_at == 1000
                assert first == repeated
                payload["energy_sample"] = {"observed_at": 1010.0, "value": 101.0}
                client.request = AsyncMock(return_value=deepcopy(payload))
                await client.execute("set_current", value=8)
                next_poll = client.apply_snapshot(payload)
                assert next_poll.energy_read
                assert next_poll.energy_observed_at == 1010
                assert next_poll.get("energy_total") == 101
                assert not client.apply_snapshot(payload).energy_read
                payload["energy_sample"] = {"observed_at": 1020.0, "value": None}
                failed_meter = client.apply_snapshot(payload)
                assert failed_meter.energy_read
                assert failed_meter.get("energy_total") is None
                assert failed_meter.energy_observed_at == 1020
                payload["energy_sample"] = None
                absent = client.apply_snapshot(payload)
                assert not absent.energy_read
                assert absent.energy_observed_at is None
                payload.pop("energy_sample")
                old = client.apply_snapshot(payload)
                assert not old.energy_read
                assert old.energy_observed_at is None
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("sample", [
    [], {}, {"observed_at": 1000}, {"value": 100},
    {"observed_at": 1000, "value": 100, "extra": True},
    *[{"observed_at": stamp, "value": 100} for stamp in (True, "1000", -1, 253402300800, float("inf"), float("nan"))],
    *[{"observed_at": 1000, "value": value} for value in (True, "100", -1, float("inf"), float("nan"), 10**1000)],
])
def test_malformed_remote_energy_sample_cannot_be_published(tmp_path, sample):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                payload["energy_sample"] = sample
                with pytest.raises(ServiceConnectionError, match=r"snapshot \(energy_sample\)"):
                    client.apply_snapshot(payload)
                assert not client.connected
                assert client._last_energy_observed_at is None
        finally:
            await service.stop()

    asyncio.run(run())


def test_remote_coordinator_observes_success_and_failed_poll(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                data = client.apply_snapshot(service.snapshot())
                client.update = AsyncMock(return_value=data)
                coordinator = ServiceCoordinator(
                    FakeHass(str(tmp_path)), FakeConfigEntry(), client=client,
                    scan_interval=5,
                )
                callback = AsyncMock()
                coordinator.observe_estimates = callback
                assert await coordinator._async_update_data() is data
                callback.assert_awaited_once_with(data)
                callback.reset_mock()
                client.update = AsyncMock(side_effect=AmtronConnectionError("offline"))
                with pytest.raises(UpdateFailed):
                    await coordinator._async_update_data()
                callback.assert_awaited_once_with(None)
                coordinator.observe_estimates = None
                with pytest.raises(UpdateFailed):
                    await coordinator._async_update_data()
        finally:
            await service.stop()

    asyncio.run(run())


def test_remote_coordinator_reads_a_queued_command_when_it_is_due(tmp_path, monkeypatch):
    async def run():
        scheduled: list[tuple[float, object]] = []
        cancelled: list[int] = []

        def call_later(_hass, delay, action):
            scheduled.append((delay, action))
            index = len(scheduled) - 1
            return lambda: cancelled.append(index)

        monkeypatch.setattr(coordinator_module, "async_call_later", call_later)
        async with ClientSession() as session:
            client = ServiceClient(session, "http://service:8765", TOKEN)
            coordinator = ServiceCoordinator(
                FakeHass(str(tmp_path)), FakeConfigEntry(), client=client,
                scan_interval=5,
            )
            coordinator.async_request_refresh = AsyncMock()
            now = datetime.now(UTC)
            coordinator._follow_pending(now + timedelta(seconds=2))
            assert len(scheduled) == 1
            assert 2.5 < scheduled[0][0] <= 3.0
            # A due time beyond the next poll is left to that poll.
            coordinator._follow_pending(now + timedelta(seconds=30))
            assert (len(scheduled), cancelled) == (1, [0])
            coordinator._follow_pending(now - timedelta(seconds=5))
            coordinator._follow_pending(now + timedelta(seconds=1))
            scheduled[-1][1](now)
            await asyncio.sleep(0)
            coordinator.async_request_refresh.assert_awaited_once()
            assert coordinator._pending_refresh is None
            coordinator._follow_pending(now + timedelta(seconds=1))
            await coordinator.async_shutdown()
            assert cancelled[-1] == len(scheduled) - 1

    asyncio.run(run())


def test_the_service_measures_a_plug_cycle_and_answers_for_any_span(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            try:
                client = ServiceClient(transport=EmbeddedTransport(ServiceApi(service)))
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 100)
                await observe(service, transport, clock, 1020, EvseState.CHARGING, 104)
                # A poll without an answer is a gap the service judges itself.
                polling, service._poller.async_poll = service._poller.async_poll, AsyncMock(
                    side_effect=AmtronConnectionError("offline"),
                )
                clock[0] = 1025
                with pytest.raises(AmtronConnectionError):
                    await service.refresh()
                service._poller.async_poll = polling
                await observe(service, transport, clock, 1030, EvseState.IDLE, 106)
                assert service.snapshot()["charge_meter"] == {
                    "known_since": 1000, "present": False,
                    "plugged_at": None, "charging_at": None, "note": None,
                    "last": [1010, 1030], "last_note": None,
                }
                data = client.apply_snapshot(service.snapshot())
                assert data.charge_meter["last"] == (1010, 1030)
                assert await client.async_energy_between(1010, 1030) == pytest.approx(6)
                # The meter is not read on every poll: between two readings it rises evenly.
                assert await client.async_energy_between(1020, 1030) == pytest.approx(3)
                # Before the first reading, and after the last: nobody measured that.
                assert await client.async_energy_between(900, 1030) is None
                assert await client.async_energy_between(1010, 1100) is None
                assert client.connected
                api = ServiceApi(service)
                for payload in (
                    {}, {"start": 1010}, {"start": "1010", "end": 1030},
                    {"start": True, "end": 1030}, {"start": 1010, "end": float("inf")},
                    {"start": 1010, "end": 10**400},
                ):
                    assert await api.dispatch("POST", "/v1/energy", payload) == (400, {"error": "invalid_request"})
                # An answer that is no energy does not pass for one.
                api.dispatch = AsyncMock(return_value=(200, {"energy_kwh": "6"}))
                broken = ServiceClient(transport=EmbeddedTransport(api))
                with pytest.raises(AmtronConnectionError, match="invalid energy span"):
                    await broken.async_energy_between(1010, 1030)
            finally:
                await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("meter", [None, {}, {**ChargeMeter().snapshot(), "last": [2, 1]}])
def test_malformed_remote_charge_meter_cannot_be_published(tmp_path, meter):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                payload = service.snapshot()
                payload["charge_meter"] = meter
                with pytest.raises(ServiceConnectionError, match=r"snapshot \(charge_meter\)"):
                    client.apply_snapshot(payload)
                assert not client.connected
        finally:
            await service.stop()

    asyncio.run(run())
