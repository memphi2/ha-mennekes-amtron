"""What one poll of the wallbox reads, and what the service's state shows HA.

The first part covers the poller the service runs on the bus; the second
the coordinator that fetches the service's state for Home Assistant.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mennekes_amtron_rtu import coordinator as coordinator_module
from custom_components.mennekes_amtron_rtu.coordinator import ServiceCoordinator
from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronBusError,
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.data import (
    ConnectionState,
    DeviceIdentity,
)
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from custom_components.mennekes_amtron_rtu.device.poller import DevicePoller
from custom_components.mennekes_amtron_rtu.device.register_blocks import (
    REGISTER_BLOCKS,
    SLOW_BLOCK_INTERVAL_SECONDS,
    BlockCadence,
)
from custom_components.mennekes_amtron_rtu.device.runtime_events import (
    EventTimeline,
    EventType,
)
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
from tests.ha_fakes import FakeConfigEntry, FakeHass


class _Polling:
    """Poll like the service does: each poll builds on the previous one."""

    def __init__(self, poller: DevicePoller, identity: DeviceIdentity) -> None:
        self._poller = poller
        self.identity = identity
        self.data: WallboxData | None = None
        self.last_update_success = True

    def current_data(self) -> WallboxData:
        return self.data or WallboxData()

    async def _async_update_data(self) -> WallboxData:
        try:
            data = await self._poller.async_poll(
                self.current_data(), last_success=self.last_update_success
            )
        except AmtronBusError:
            self.last_update_success = False
            raise
        self.last_update_success = True
        return data

    async def async_refresh(self) -> None:
        with contextlib.suppress(AmtronBusError):
            self.data = await self._async_update_data()


async def _coordinator(
    transport: FakeModbusClient,
    *,
    layout: int = R.LAYOUT_V01_03,
    connection_state: ConnectionState | None = None,
    monotonic: Any = None,
    measurement_interval: float = 5,
    timeline: EventTimeline | None = None,
) -> _Polling:
    client = MennekesModbusClient(
        SerialConfig(port="/dev/fake"), client_factory=lambda _config: transport
    )
    await client.async_connect()
    identity = DeviceIdentity(layout_version=layout)
    return _Polling(
        DevicePoller(
            client,
            identity,
            connection_state=connection_state or ConnectionState(),
            measurement_interval=measurement_interval,
            timeline=timeline,
            **({"monotonic": monotonic} if monotonic else {}),
        ),
        identity,
    )


def test_a_poll_reads_every_supported_block() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport)
        data = await coordinator._async_update_data()
        assert data.failed_blocks == ()
        assert data.get("evse_state") == 1
        assert data.get("energy_total") == pytest.approx(1234.5)
        assert coordinator.identity.layout_version == R.LAYOUT_V01_03
        assert len(transport.reads) == 18

    asyncio.run(run())


def test_recovery_observes_fresh_error_status_only() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport)
        transport.bank[R.ERROR_CODE.address] = 200
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.fresh(R.ERROR_CODE.key) == 200
        transport.bank[R.ERROR_CODE.address] = 0
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.fresh(R.ERROR_CODE.key) == 0

        # An unreadable error register must not confirm a cached zero.
        transport.read_exceptions[R.ERROR_CODE.address] = 0x04
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.fresh(R.ERROR_CODE.key) is None
        transport.read_exceptions[0x0100] = raise_connection_error()
        with pytest.raises(AmtronBusError):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_an_older_layout_polls_fewer_blocks() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank(layout=R.LAYOUT_V01_00))
        coordinator = await _coordinator(transport, layout=R.LAYOUT_V01_00)
        data = await coordinator._async_update_data()
        assert data.get("energy_total") is None
        assert len(transport.reads) < 18

    asyncio.run(run())


def test_a_single_failing_block_does_not_lose_the_snapshot() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        transport.read_exceptions[0x1000] = 0x04
        coordinator = await _coordinator(transport)
        data = await coordinator._async_update_data()
        assert data.failed_blocks == ("statistics",)
        assert data.get("evse_state") == 1

    asyncio.run(run())


def test_a_lost_connection_fails_the_update() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        transport.read_exceptions[0x0100] = raise_connection_error()
        coordinator = await _coordinator(transport)
        with pytest.raises(AmtronBusError):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_an_entirely_silent_device_fails_the_update() -> None:
    async def run() -> None:
        transport = FakeModbusClient()
        for block_address in (
            0x0000,
            0x0013,
            0x001B,
            0x0100,
            0x0108,
            0x0114,
            0x0300,
            0x030A,
            0x030C,
            0x030D,
            0x0500,
            0x0900,
            0x0B00,
            0x0B06,
            0x0D02,
            0x0E00,
            0x0E01,
            0x1000,
        ):
            transport.read_exceptions[block_address] = 0x04
        coordinator = await _coordinator(transport)
        with pytest.raises(AmtronBusError, match="no register block"):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_a_lost_and_a_recovered_connection_are_each_recorded_once() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        timeline = EventTimeline()
        coordinator = await _coordinator(transport, timeline=timeline)
        await coordinator._async_update_data()

        def recorded() -> list[str]:
            return [event["event"] for event in timeline.snapshot()]

        # a block read on every poll, not one of the slow configuration ones
        transport.read_exceptions[0x0100] = raise_connection_error()
        for _ in range(3):
            with pytest.raises(AmtronBusError):
                await coordinator._async_update_data()
        assert recorded().count(EventType.CONNECTION_LOST.value) == 1
        assert EventType.CONNECTION_RECOVERED.value not in recorded()

        transport.read_exceptions.clear()
        await coordinator._async_update_data()
        await coordinator._async_update_data()
        assert recorded().count(EventType.CONNECTION_RECOVERED.value) == 1

    asyncio.run(run())


def test_current_data_is_empty_before_the_first_poll() -> None:
    async def run() -> None:
        coordinator = await _coordinator(FakeModbusClient(device_bank()))
        assert coordinator.current_data().values == {}

    asyncio.run(run())


def test_configuration_blocks_are_not_read_on_every_poll() -> None:
    """They change when somebody reconfigures the wallbox, not while charging."""

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        clock = [1000.0]
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])

        await coordinator.async_refresh()
        first = len(transport.reads)
        assert first == len(REGISTER_BLOCKS)

        transport.reads.clear()
        await coordinator.async_refresh()
        fast = {
            block.address
            for block in REGISTER_BLOCKS
            if block.cadence is BlockCadence.FAST
        }
        assert {address for address, _ in transport.reads} == fast
        assert len(transport.reads) < first

        # the configuration values survive a poll that did not read them
        assert coordinator.data.get("serial_number") == "ABC123456789"
        assert coordinator.data.get("ems_fallback_current") == 6

        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS
        transport.reads.clear()
        await coordinator.async_refresh()
        assert len(transport.reads) == len(REGISTER_BLOCKS)

    asyncio.run(run())


def test_a_configuration_change_is_picked_up_at_the_slow_cadence() -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        clock = [1000.0]
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        await coordinator.async_refresh()
        assert coordinator.data.get("ems_fallback_current") == 6

        transport.bank[R.EMS_FALLBACK_CURRENT.address] = 16
        await coordinator.async_refresh()
        assert coordinator.data.get("ems_fallback_current") == 6

        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS
        await coordinator.async_refresh()
        assert coordinator.data.get("ems_fallback_current") == 16

    asyncio.run(run())


def test_a_failing_block_drops_its_values_instead_of_freezing_them() -> None:
    """A block that keeps failing has no current value.

    One disturbed read is bridged so entities do not flicker unavailable.
    Beyond that, a frozen measurement reported as live is worse than none.
    """

    async def run() -> None:
        # A connected vehicle keeps the measurement block on the fast cadence;
        # this test is about the failure path, not about the cadence.
        transport = FakeModbusClient(device_bank(evse_state=EvseState.CHARGING))
        clock = [0.0]
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        first = await coordinator._async_update_data()
        assert first.has("voltage_l1")
        coordinator.data = first

        transport.read_exceptions[0x0500] = 0x04
        clock[0] += 5
        second = await coordinator._async_update_data()
        assert second.failed_blocks == ("measurements",)
        assert second.has("voltage_l1")
        assert second.fresh("voltage_l1") is None
        # The blocks that did answer are untouched.
        assert second.fresh("evse_state") == EvseState.CHARGING
        coordinator.data = second

        clock[0] += 30
        third = await coordinator._async_update_data()
        assert not third.has("voltage_l1")
        assert third.get("voltage_l1") is None

    asyncio.run(run())


def test_losing_the_vehicle_status_block_is_logged_once(caplog) -> None:
    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.CHARGING))
        clock = [0.0]
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        transport.read_exceptions[0x0100] = 0x04
        for _ in range(3):
            clock[0] += 31
            coordinator.data = await coordinator._async_update_data()
        assert not coordinator.data.has("evse_state")
        assert caplog.text.count("Connection: block status unreadable for over 30 seconds") == 1
        transport.read_exceptions.clear()
        clock[0] += 5
        coordinator.data = await coordinator._async_update_data()
        assert "Connection: block status readable again" in caplog.text

    caplog.set_level(logging.INFO)
    asyncio.run(run())


def test_a_slow_block_that_is_not_due_keeps_its_values() -> None:
    """Dropping stale values must not drop the ones carried on purpose."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.has("energy_total")

        clock[0] += 1
        later = await coordinator._async_update_data()
        slow = [
            block for block in REGISTER_BLOCKS if block.cadence is BlockCadence.SLOW
        ]
        assert slow
        assert later.failed_blocks == ()
        assert later.has("energy_total")

    asyncio.run(run())


def test_a_failing_slow_block_drops_its_values_when_it_was_due() -> None:
    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.has("energy_total")

        transport.read_exceptions[0x1000] = 0x04
        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS + 1
        later = await coordinator._async_update_data()
        assert later.failed_blocks == ("statistics",)
        assert not later.has("energy_total")

    asyncio.run(run())


# --- the measurement blocks follow the vehicle, not the clock --------------


def _measurement_reads(transport: FakeModbusClient) -> int:
    measurements = next(
        block for block in REGISTER_BLOCKS if block.name == "measurements"
    )
    return sum(
        1 for address, _count in transport.reads if address == measurements.address
    )


def test_an_idle_wallbox_stops_polling_the_measurements() -> None:
    """Three voltages read every few seconds are a database row every few
    seconds, describing a wallbox that has nothing plugged into it."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.IDLE))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 1

        for _ in range(5):
            clock[0] += 5
            coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 1
        # The values stay in the snapshot, so the entities stay available.
        assert coordinator.data.has("voltage_l1")

    asyncio.run(run())


def test_a_connected_vehicle_keeps_the_measurements_on_the_fast_cadence() -> None:
    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.CHARGING))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        for _ in range(6):
            clock[0] += 5
            coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 6

    asyncio.run(run())


def test_a_vehicle_that_is_only_plugged_in_already_counts() -> None:
    """Everything above idle means something is connected."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.EV_CONNECTED))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        clock[0] += 5
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 2

    asyncio.run(run())


def test_an_unknown_state_is_treated_as_connected() -> None:
    """Being wrong this way costs bus traffic; the other way hides a charge."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        # No poll has happened, so the snapshot carries no state at all.
        assert coordinator.current_data().get("evse_state") is None
        transport.read_exceptions[0x0100] = 0x04
        coordinator.data = await coordinator._async_update_data()
        clock[0] += 5
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 2

    asyncio.run(run())


def test_an_idle_wallbox_still_refreshes_the_measurements_every_minute() -> None:
    """Demoted, not dropped: the mains voltage stays visible."""

    clock = [0.0]

    async def run() -> None:
        transport = FakeModbusClient(device_bank(evse_state=EvseState.IDLE))
        coordinator = await _coordinator(transport, monotonic=lambda: clock[0])
        coordinator.data = await coordinator._async_update_data()
        clock[0] += SLOW_BLOCK_INTERVAL_SECONDS + 1
        coordinator.data = await coordinator._async_update_data()
        assert _measurement_reads(transport) == 2

    asyncio.run(run())


@pytest.mark.parametrize("state", [EvseState.CHARGING, EvseState.EV_CONNECTED])
def test_measurements_are_throttled_while_status_stays_fast(state: EvseState) -> None:
    async def run() -> None:
        from tests.fakes import put

        clock = [0.0]
        transport = FakeModbusClient(device_bank(evse_state=state))
        coordinator = await _coordinator(
            transport, monotonic=lambda: clock[0], measurement_interval=30
        )
        coordinator.data = await coordinator._async_update_data()
        for tick in range(5, 30, 5):
            clock[0] = tick
            put(transport.bank, R.VOLTAGE_L1, 231.0)
            put(transport.bank, R.CHARGING_CURRENT_EMS, 7.0 + tick / 10)
            coordinator.data = await coordinator._async_update_data()
            assert coordinator.data.get("voltage_l1") != 231.0
            assert coordinator.data.get("charging_current_ems") == pytest.approx(
                7 + tick / 10
            )
        assert _measurement_reads(transport) == 1
        clock[0] = 30
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.get("voltage_l1") == 231.0
        assert _measurement_reads(transport) == 2

    asyncio.run(run())


def test_start_and_stop_refresh_measurements_and_energy_in_the_same_poll() -> None:
    async def run() -> None:
        from tests.fakes import put

        clock = [0.0]
        transport = FakeModbusClient(device_bank())
        coordinator = await _coordinator(
            transport, monotonic=lambda: clock[0], measurement_interval=30
        )
        coordinator.data = await coordinator._async_update_data()
        for state, power, total in (
            (EvseState.CHARGING, 11000, 1234.51),
            (EvseState.EV_CONNECTED, 0, 1234.52),
            (EvseState.IDLE, 0, 1234.52),
        ):
            clock[0] += 5
            put(transport.bank, R.EVSE_STATE, state)
            put(transport.bank, R.POWER_TOTAL, power)
            put(transport.bank, R.ENERGY_TOTAL, total)
            coordinator.data = await coordinator._async_update_data()
            assert coordinator.data.get("evse_state") == state
            assert coordinator.data.get("power_total") == power
            assert coordinator.data.get("energy_total") == pytest.approx(total)
        assert _measurement_reads(transport) == 4

    asyncio.run(run())


def test_a_failed_measurement_stays_unavailable_until_read_again() -> None:
    async def run() -> None:
        clock = [0.0]
        transport = FakeModbusClient(device_bank(evse_state=EvseState.CHARGING))
        coordinator = await _coordinator(
            transport, monotonic=lambda: clock[0], measurement_interval=30
        )
        coordinator.data = await coordinator._async_update_data()
        transport.read_exceptions[0x0500] = 4
        clock[0] = 31
        coordinator.data = await coordinator._async_update_data()
        transport.read_exceptions.clear()
        clock[0] = 35
        coordinator.data = await coordinator._async_update_data()
        assert not coordinator.data.has("voltage_l1")
        assert coordinator.data.failed_blocks == ("measurements",)
        clock[0] = 36
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.has("voltage_l1")
        assert coordinator.data.failed_blocks == ()

    asyncio.run(run())


# --- the coordinator that shows the service's state ------------------------


class _Client:
    """The part of the service client the coordinator uses."""

    def __init__(self, *results: object) -> None:
        self.identity = DeviceIdentity(serial_number="ABC123456789")
        self.on_auth_error = None
        self._results = list(results)

    async def update(self) -> WallboxData:
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _service_coordinator(client: _Client) -> ServiceCoordinator:
    return ServiceCoordinator(FakeHass(), FakeConfigEntry(), client=client, scan_interval=5)


def test_the_coordinator_shows_the_services_identity_and_state() -> None:
    async def run() -> None:
        data = WallboxData(values={"evse_state": 1})
        coordinator = _service_coordinator(_Client(data))
        assert coordinator.identity.serial_number == "ABC123456789"
        assert coordinator.current_data().values == {}
        observed: list[WallboxData | None] = []

        async def observe(value: WallboxData | None) -> None:
            observed.append(value)

        coordinator.observe_session = observe
        assert await coordinator._async_update_data() is data
        assert observed == [data]

    asyncio.run(run())


@pytest.mark.parametrize(("reason", "error", "key"), [
    ("service_auth_failed", ConfigEntryAuthFailed, "service_auth_failed"),
    ("service_identity_changed", UpdateFailed, "device_identity_changed"),
    ("service_version_mismatch", UpdateFailed, "service_version_mismatch"),
    ("service_not_ready", UpdateFailed, None),
])
def test_each_service_failure_has_its_own_reason(reason, error, key) -> None:
    async def run() -> None:
        coordinator = _service_coordinator(_Client(ServiceConnectionError(reason, "failed")))
        observed: list[WallboxData | None] = []

        async def observe(value: WallboxData | None) -> None:
            observed.append(value)

        coordinator.observe_session = observe
        with pytest.raises(error) as caught:
            await coordinator._async_update_data()
        assert caught.value.translation_key == key
        # A failed poll still reaches the estimates, as a gap.
        assert observed == [None]

    asyncio.run(run())


def test_a_lost_service_fails_the_update_without_an_estimate_observer() -> None:
    async def run() -> None:
        coordinator = _service_coordinator(_Client(AmtronConnectionError("lost")))
        with pytest.raises(UpdateFailed, match="lost"):
            await coordinator._async_update_data()

    asyncio.run(run())


def test_a_queued_command_is_read_back_when_it_is_due(monkeypatch) -> None:
    async def run() -> None:
        scheduled: list[tuple[float, Any]] = []
        cancelled: list[int] = []

        def call_later(_hass, delay, action):
            scheduled.append((delay, action))
            index = len(scheduled) - 1
            return lambda: cancelled.append(index)

        monkeypatch.setattr(coordinator_module, "async_call_later", call_later)
        now = datetime.now(UTC)
        soon = WallboxData(pending_change_until=now + timedelta(seconds=2))
        later = WallboxData(pending_change_until=now + timedelta(seconds=60))
        coordinator = _service_coordinator(_Client(soon, soon, later, WallboxData()))
        await coordinator._async_update_data()
        assert len(scheduled) == 1
        assert 2 < scheduled[0][0] < 3.5
        # A newer poll replaces the timer instead of adding one.
        await coordinator._async_update_data()
        assert cancelled == [0]
        assert len(scheduled) == 2
        # A due time beyond the next poll needs no timer of its own.
        await coordinator._async_update_data()
        assert cancelled == [0, 1]
        assert len(scheduled) == 2
        await coordinator._async_update_data()
        assert len(scheduled) == 2

        refreshed: list[bool] = []

        async def request_refresh() -> None:
            refreshed.append(True)

        coordinator.async_request_refresh = request_refresh
        coordinator._pending_refresh = lambda: cancelled.append(99)
        scheduled[1][1](now)
        await asyncio.sleep(0)
        assert refreshed == [True]
        assert coordinator._pending_refresh is None

        coordinator._pending_refresh = lambda: cancelled.append(100)
        await coordinator.async_shutdown()
        assert cancelled[-1] == 100

    asyncio.run(run())


def test_without_a_poll_interval_no_due_time_is_followed(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(
            coordinator_module, "async_call_later",
            lambda *_: pytest.fail("no timer without an interval"),
        )
        soon = WallboxData(pending_change_until=datetime.now(UTC) + timedelta(seconds=2))
        coordinator = _service_coordinator(_Client(soon))
        coordinator.update_interval = None
        await coordinator._async_update_data()

    asyncio.run(run())


def test_a_new_authentication_failure_is_published_even_while_unavailable(monkeypatch) -> None:
    # Translating the message needs a running Home Assistant.
    monkeypatch.setattr(
        coordinator_module, "ConfigEntryAuthFailed",
        lambda **_kwargs: ConfigEntryAuthFailed("service authentication failed"),
    )

    async def run() -> None:
        client = _Client()
        coordinator = _service_coordinator(client)
        notified: list[bool] = []
        coordinator.async_add_listener(lambda: notified.append(coordinator.last_update_success))
        client.on_auth_error(True)
        assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
        # Already available before: HA itself notified the listeners once.
        assert notified == [False]
        client.on_auth_error(True)
        assert notified == [False, False]
        # A repeated report of the same failure stays quiet.
        client.on_auth_error(False)
        assert notified == [False, False]

    asyncio.run(run())


def test_a_rejected_gateway_password_asks_for_it_again_instead_of_only_failing() -> None:
    async def run() -> None:
        rejected = [False]
        coordinator = ServiceCoordinator(
            FakeHass(), FakeConfigEntry(),
            client=_Client(AmtronConnectionError("lost"), AmtronConnectionError("lost")),
            scan_interval=5, credentials_rejected=lambda: rejected[0],
        )
        # A lost wallbox stays an ordinary failed update.
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        rejected[0] = True
        with pytest.raises(ConfigEntryAuthFailed) as raised:
            await coordinator._async_update_data()
        assert raised.value.translation_key == "gateway_authentication_failed"

    asyncio.run(run())
