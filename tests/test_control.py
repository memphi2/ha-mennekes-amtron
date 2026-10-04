"""Tests for the write choke point.

The first group is the whole reason this integration exists: ``0x0302`` means
"no limitation" at 0 A and "pause" between 0.01 A and 5.99 A, so neither value
may ever be reachable by accident.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.client import MennekesModbusClient, SerialConfig
from custom_components.mennekes_amtron.control import AmtronControl, ControlOptions
from custom_components.mennekes_amtron.data import (
    DeviceIdentity,
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron.enums import (
    EvseState,
    RequestedPhases,
    SolarChargingMode,
)
from tests.fakes import FakeModbusClient, device_bank, raise_connection_error

CONFIG = SerialConfig(port="/dev/fake", device_id=50)


class Harness:
    """One wired-up control object over a fake bus."""

    def __init__(
        self,
        *,
        read_only: bool = False,
        current_cap: float | None = None,
        phase_options: int | None = 2,
        values: dict[str, object] | None = None,
        clock: list[float] | None = None,
    ) -> None:
        self.transport = FakeModbusClient(device_bank())
        self.clock = clock if clock is not None else [1000.0]
        self.client = MennekesModbusClient(
            CONFIG,
            client_factory=lambda _config: self.transport,
            monotonic=lambda: self.clock[0],
        )
        self.diagnostics = WriteDiagnostics()
        self.identity = DeviceIdentity(
            layout_version=R.LAYOUT_V01_03,
            phase_options_hw=phase_options,
            max_evse_current=16.0,
        )
        self.snapshot = WallboxData(values=dict(values or {}))
        self.refreshes = 0
        self.slept: list[float] = []
        self.control = AmtronControl(
            self.client,
            diagnostics=self.diagnostics,
            identity=self.identity,
            options=ControlOptions(read_only=read_only, current_cap=current_cap),
            state=lambda: self.snapshot,
            request_refresh=self._refresh,
            sleep=self._sleep,
        )

    async def _refresh(self) -> None:
        self.refreshes += 1

    async def _sleep(self, delay: float) -> None:
        self.slept.append(delay)
        self.clock[0] += delay
        await asyncio.sleep(0)

    async def connect(self) -> AmtronControl:
        await self.client.async_connect()
        return self.control

    def written(self, spec: R.RegisterSpec) -> list[list[int]]:
        return [words for address, words in self.transport.writes if address == spec.address]


def _run(coro_factory) -> Harness:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await coro_factory(control)

    asyncio.run(run())
    return harness


# --- the 0 A footgun -------------------------------------------------------


def test_zero_ampere_is_refused_without_an_explicit_opt_in() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_set_charging_current(0)
        assert excinfo.value.translation_key == "charging_current_zero"

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS) == []


def test_zero_ampere_is_written_only_with_the_opt_in() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(0, allow_unlimited=True)

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS) == [[0, 0]]


@pytest.mark.parametrize("value", [0.01, 1.0, 3.5, 5.99])
def test_the_invalid_band_is_always_refused(value: float) -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_set_charging_current(value)
        assert excinfo.value.translation_key == "charging_current_invalid"

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS) == []


def test_the_invalid_band_is_refused_even_with_the_opt_in() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        with pytest.raises(ServiceValidationError):
            await control.async_set_charging_current(1.0, allow_unlimited=True)

    asyncio.run(run())


# --- clamping and setpoints ------------------------------------------------


def test_a_request_above_the_device_maximum_is_clamped() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(32)
        assert control.charging_setpoint == 16.0

    asyncio.run(run())


def test_the_user_cap_lowers_the_maximum() -> None:
    harness = Harness(current_cap=10.0)

    async def run() -> None:
        control = await harness.connect()
        assert control.max_charging_current == 10.0
        await control.async_set_charging_current(16)
        assert control.charging_setpoint == 10.0

    asyncio.run(run())


def test_without_any_known_limit_the_minimum_is_used() -> None:
    harness = Harness()
    harness.identity.max_evse_current = None
    assert harness.control.max_charging_current == 6.0


# --- pause and resume ------------------------------------------------------


def test_pausing_writes_the_documented_pause_value() -> None:
    harness = Harness(values={"charging_current_ems": 16.0})

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        assert control.charging_setpoint == 16.0

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS)


def test_resuming_restores_the_stored_setpoint() -> None:
    harness = Harness(values={"charging_current_ems": 12.0}, clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.clock[0] += 400
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        assert control.is_paused
        await control.async_resume_charging()

    asyncio.run(run())
    from custom_components.mennekes_amtron.decode import decode_register

    last = harness.written(R.CHARGING_CURRENT_EMS)[-1]
    assert decode_register(R.CHARGING_CURRENT_EMS, last) == 12.0


def test_resuming_without_a_setpoint_uses_the_maximum() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_resume_charging()

    asyncio.run(run())
    from custom_components.mennekes_amtron.decode import decode_register

    last = harness.written(R.CHARGING_CURRENT_EMS)[-1]
    assert decode_register(R.CHARGING_CURRENT_EMS, last) == 16.0


def test_pause_detection_ignores_valid_and_missing_values() -> None:
    harness = Harness(values={"charging_current_ems": 16.0})
    assert not harness.control.is_paused
    harness.snapshot = WallboxData()
    assert not harness.control.is_paused
    harness.snapshot = WallboxData(values={"charging_current_ems": 0.0})
    assert not harness.control.is_paused


# --- rate limiting ---------------------------------------------------------


def test_a_fast_current_change_is_deferred_not_rejected() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(6)
        await control.async_set_charging_current(8)
        await control.async_set_charging_current(10)
        for _ in range(10):
            await asyncio.sleep(0)

    asyncio.run(run())
    from custom_components.mennekes_amtron.decode import decode_register

    writes = harness.written(R.CHARGING_CURRENT_EMS)
    assert len(writes) == 2
    assert decode_register(R.CHARGING_CURRENT_EMS, writes[0]) == 6.0
    assert decode_register(R.CHARGING_CURRENT_EMS, writes[1]) == 10.0
    assert harness.diagnostics.writes_rate_limited == 2


def test_a_fast_mode_change_is_rejected_with_a_wait_time() -> None:
    harness = Harness(values={"charging_current_ems": 16.0})

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_resume_charging()
        assert excinfo.value.translation_key == "write_rate_limited"

    asyncio.run(run())


def test_a_deferred_write_that_fails_is_dropped_without_killing_the_task() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(6)
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = (
            raise_connection_error()
        )
        await control.async_set_charging_current(8)
        for _ in range(10):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1


def test_shutdown_cancels_a_deferred_write() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(6)
        await control.async_set_charging_current(8)
        await control.async_shutdown()
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1


def test_shutdown_without_a_pending_write_is_a_no_op() -> None:
    asyncio.run(Harness().control.async_shutdown())


# --- the remaining commands ------------------------------------------------


def test_release_and_lock_write_their_registers() -> None:
    harness = _run(
        lambda control: _sequence(
            control.async_set_charging_release(True),
            control.async_set_lock_evse(True),
            control.async_set_solar_charging_mode(SolarChargingMode.SUNSHINE),
        )
    )
    assert harness.written(R.CHARGING_RELEASE) == [[1]]
    assert harness.written(R.LOCK_EVSE) == [[1]]
    assert harness.written(R.SOLAR_CHARGING_MODE) == [[2]]
    assert harness.refreshes == 3


def test_the_phase_switch_needs_capable_hardware() -> None:
    harness = Harness(phase_options=1)

    async def run() -> None:
        control = await harness.connect()
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert excinfo.value.translation_key == "phase_switch_unsupported"

    asyncio.run(run())
    assert harness.written(R.REQUESTED_PHASES) == []


def test_the_phase_switch_pauses_a_running_charge_first() -> None:
    harness = Harness(values={"evse_state": EvseState.CHARGING})

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS)
    assert harness.written(R.REQUESTED_PHASES) == [[1]]


def test_the_phase_switch_skips_the_pause_when_idle() -> None:
    harness = Harness(values={"evse_state": EvseState.IDLE})

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.ALL_AVAILABLE)

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS) == []
    assert harness.written(R.REQUESTED_PHASES) == [[0]]


def test_recovery_writes_the_documented_sequence_in_order() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_recover_from_error()

    asyncio.run(run())
    addresses = [address for address, _ in harness.transport.writes]
    assert addresses == [
        R.HEARTBEAT.address,
        R.CHARGING_RELEASE.address,
        R.CHARGING_CURRENT_EMS.address,
    ]
    assert harness.diagnostics.recoveries == 1


def test_restart_is_refused_outside_the_idle_state() -> None:
    harness = Harness(values={"evse_state": EvseState.CHARGING})

    async def run() -> None:
        control = await harness.connect()
        with pytest.raises(ServiceValidationError) as excinfo:
            await control.async_restart()
        assert excinfo.value.translation_key == "restart_requires_idle"

    asyncio.run(run())
    assert harness.written(R.SYSTEM_RESTART) == []


def test_restart_writes_the_magic_value_when_idle() -> None:
    harness = Harness(values={"evse_state": EvseState.IDLE})

    async def run() -> None:
        control = await harness.connect()
        await control.async_restart()

    asyncio.run(run())
    assert harness.written(R.SYSTEM_RESTART) == [[0xBB]]


# --- modes and failures ----------------------------------------------------


def test_read_only_mode_refuses_every_write() -> None:
    harness = Harness(read_only=True, values={"evse_state": EvseState.IDLE})

    async def run() -> None:
        control = await harness.connect()
        assert control.read_only
        assert control.options.read_only
        for call in (
            control.async_set_charging_current(10),
            control.async_pause_charging(),
            control.async_resume_charging(),
            control.async_set_charging_release(True),
            control.async_set_lock_evse(True),
            control.async_set_solar_charging_mode(SolarChargingMode.FAST),
            control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE),
            control.async_recover_from_error(),
            control.async_restart(),
        ):
            with pytest.raises(ServiceValidationError) as excinfo:
                await call
            assert excinfo.value.translation_key == "read_only_mode"

    asyncio.run(run())
    assert harness.transport.writes == []


def test_a_bus_failure_becomes_a_translated_error_and_is_counted() -> None:
    harness = Harness()
    harness.transport.write_exceptions[R.CHARGING_RELEASE.address] = (
        raise_connection_error()
    )

    async def run() -> None:
        control = await harness.connect()
        with pytest.raises(HomeAssistantError) as excinfo:
            await control.async_set_charging_release(True)
        assert excinfo.value.translation_key == "write_failed"

    asyncio.run(run())
    assert harness.diagnostics.writes_rejected == 1
    assert harness.diagnostics.last_error


async def _sequence(*awaitables: object) -> None:
    for awaitable in awaitables:
        await awaitable
