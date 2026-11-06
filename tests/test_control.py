"""Tests for the write choke point.

The first group is the whole reason this integration exists: ``0x0302`` means
"no limitation" at 0 A and "pause" between 0.01 A and 5.99 A, so neither value
may ever be reachable by accident.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.control import AmtronControl, ControlOptions
from custom_components.mennekes_amtron_rtu.data import (
    DeviceIdentity,
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.enums import (
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
        error_code: int = 0,
        state_change_interval_seconds: float = 0,
        phase_switch_grace_seconds: float = 5,
    ) -> None:
        self.transport = FakeModbusClient(device_bank(
            error_code=error_code,
            evse_state=(values or {}).get("evse_state", EvseState.IDLE),
            charging_current=(values or {}).get("charging_current_ems", 16.0),
        ))
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
        self.snapshot = WallboxData(values=dict(values if values is not None else {"charging_current_ems": 16.0}))
        self.refreshes = 0
        self.slept: list[float] = []
        self.control = AmtronControl(
            self.client,
            diagnostics=self.diagnostics,
            identity=self.identity,
            options=ControlOptions(
                read_only=read_only, current_cap=current_cap,
                state_change_interval_seconds=state_change_interval_seconds,
                phase_switch_grace_seconds=phase_switch_grace_seconds,
            ),
            state=lambda: self.snapshot,
            request_refresh=self._refresh,
            sleep=self._sleep,
            monotonic=lambda: self.clock[0],
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
        return [
            words for address, words in self.transport.writes if address == spec.address
        ]

    async def confirm_phase_pause(self) -> None:
        """Explicitly simulate the vehicle acknowledging the requested pause."""
        self.transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        self.transport.bank[R.CP_STATE.address] = 11
        if self.control._flush_task is not None:
            await asyncio.wait_for(self.control._flush_task, 1)


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


@pytest.mark.parametrize("value", [0.01, 1.0, 3.5, 5.99, float("nan"), float("inf")])
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
    from custom_components.mennekes_amtron_rtu.device.decode import decode_register

    last = harness.written(R.CHARGING_CURRENT_EMS)[-1]
    assert decode_register(R.CHARGING_CURRENT_EMS, last) == 12.0


def test_resuming_without_a_setpoint_uses_the_maximum() -> None:
    harness = Harness()

    async def run() -> None:
        control = await harness.connect()
        await control.async_resume_charging()

    asyncio.run(run())
    from custom_components.mennekes_amtron_rtu.device.decode import decode_register

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
    from custom_components.mennekes_amtron_rtu.device.decode import decode_register

    writes = harness.written(R.CHARGING_CURRENT_EMS)
    assert len(writes) == 2
    assert decode_register(R.CHARGING_CURRENT_EMS, writes[0]) == 6.0
    assert decode_register(R.CHARGING_CURRENT_EMS, writes[1]) == 10.0
    assert harness.diagnostics.writes_rate_limited == 2


def test_a_fast_mode_change_is_queued_without_changing_actual_pause() -> None:
    harness = Harness(values={"charging_current_ems": 16.0})

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        assert control.is_paused is True

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
        await harness.confirm_phase_pause()

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS)
    assert harness.written(R.REQUESTED_PHASES) == [[1]]


@pytest.mark.parametrize("remembered", [None, 16.0])
def test_phase_pause_restores_the_actual_pre_pause_current(remembered) -> None:
    harness = Harness(values={"charging_current_ems": 8.0, "evse_state": EvseState.CHARGING})

    async def run() -> None:
        control = await harness.connect()
        control.charging_setpoint = remembered
        observed = []

        async def refresh():
            # HA reads the remembered value during its refresh callbacks,
            # before the phase-change action has returned to its caller.
            observed.append(control.charging_setpoint)

        control._request_refresh = refresh
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        await harness.confirm_phase_pause()
        assert observed
        assert all(value == 8.0 for value in observed)
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert control.charging_setpoint == 8.0
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 301
        await control.async_resume_charging()
        from custom_components.mennekes_amtron_rtu.device.decode import decode_register

        assert decode_register(
            R.CHARGING_CURRENT_EMS, harness.written(R.CHARGING_CURRENT_EMS)[-1]
        ) == 8.0

    asyncio.run(run())


def test_a_rejected_pause_keeps_the_previously_accepted_setpoint() -> None:
    harness = Harness(values={"charging_current_ems": 8.0})

    async def run() -> None:
        control = await harness.connect()
        control.charging_setpoint = 10.0
        control.pause_requested = False
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = 3
        with pytest.raises(HomeAssistantError):
            await control.async_pause_charging()
        assert control.charging_setpoint == 10.0
        assert control.pause_requested is False

    asyncio.run(run())


def test_the_phase_switch_skips_the_pause_when_idle() -> None:
    harness = Harness(values={"evse_state": EvseState.IDLE})

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.ALL_AVAILABLE)

    asyncio.run(run())
    assert harness.written(R.CHARGING_CURRENT_EMS) == []
    assert harness.written(R.REQUESTED_PHASES) == []  # Already all phases: idempotent.


def test_recovery_writes_the_documented_sequence_in_order() -> None:
    harness = Harness(error_code=200)

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
    assert [event["event"] for event in harness.diagnostics.timeline.snapshot()] == [
        "recovery_started", "recovery_completed",
    ]


@pytest.mark.parametrize("elapsed", [5.0, 15.0])
def test_repeated_recovery_only_waits_for_the_register_interval(elapsed: float) -> None:
    async def run() -> None:
        harness = Harness(error_code=200, values={"charging_current_ems": 16.0, "error_code": 200})
        control = await harness.connect()
        await control.async_recover_from_error()
        harness.clock[0] += elapsed
        await control.async_recover_from_error()
        assert [address for address, _ in harness.transport.writes] == [
            R.HEARTBEAT.address,
            R.CHARGING_RELEASE.address,
            R.CHARGING_CURRENT_EMS.address,
        ] * 2
        assert harness.diagnostics.recoveries == 2

    asyncio.run(run())


def test_recovery_without_resuming_does_not_delay_a_later_pause() -> None:
    async def run() -> None:
        harness = Harness(error_code=200, values={"charging_current_ems": 16.0})
        control = await harness.connect()
        await control.async_recover_from_error()
        harness.clock[0] += 15
        await control.async_pause_charging()
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2

    asyncio.run(run())


def test_recovery_of_a_resumed_charge_does_not_wait_five_minutes() -> None:
    async def run() -> None:
        harness = Harness(error_code=200, values={"charging_current_ems": 1.0})
        control = await harness.connect()
        await control.async_resume_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 16.0})
        harness.clock[0] += 15
        await control.async_recover_from_error()
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2
        assert harness.diagnostics.recoveries == 1

    asyncio.run(run())


def test_recovery_retains_a_pause_and_obeys_the_write_floor() -> None:
    async def run() -> None:
        harness = Harness(error_code=200, values={"charging_current_ems": 16.0})
        control = await harness.connect()
        await control.async_pause_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 1
        with pytest.raises(ServiceValidationError) as err:
            await control.async_recover_from_error()
        assert err.value.translation_key == "write_rate_limited"
        assert err.value.translation_placeholders == {"seconds": "4"}
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert harness.written(R.HEARTBEAT) == []
        assert harness.written(R.CHARGING_RELEASE) == []
        assert harness.diagnostics.recoveries == 0

        harness.clock[0] += 4
        await control.async_recover_from_error()
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2
        assert harness.diagnostics.recoveries == 1
        assert [decode_register(R.CHARGING_CURRENT_EMS, words) for words in harness.written(R.CHARGING_CURRENT_EMS)] == [1.0, 1.0]
        assert control.charging_setpoint == 16
        assert control.pause_requested is True

        harness.snapshot = WallboxData(values={"charging_current_ems": 16.0})
        harness.clock[0] += 1
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2
        await asyncio.wait_for(control._flush_task, 1)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 3
        assert harness.clock[0] == 1010

    asyncio.run(run())


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
    harness = Harness(error_code=200, read_only=True, values={"evse_state": EvseState.IDLE})

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


# --- the two rules that meet on 0x0302 -------------------------------------


def test_a_setpoint_change_does_not_block_the_pause_switch() -> None:
    """The five-second register floor must not borrow the five-minute one.

    Both rules used to be keyed on the register, so moving the slider made
    the pause switch refuse for the next five seconds.
    """

    harness = Harness(clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(10)
        harness.clock[0] += 10
        harness.snapshot = WallboxData(values={"charging_current_ems": 10.0})
        await control.async_pause_charging()

    asyncio.run(run())
    from custom_components.mennekes_amtron_rtu.device.decode import decode_register

    writes = harness.written(R.CHARGING_CURRENT_EMS)
    assert len(writes) == 2
    assert decode_register(R.CHARGING_CURRENT_EMS, writes[-1]) == 1.0


def test_a_setpoint_change_cannot_undo_a_pause_early() -> None:
    """Writing a real limit while paused is a resume, and waits like one."""

    harness = Harness(values={"charging_current_ems": 16.0}, clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 1
        await control.async_set_charging_current(12)
        assert control.pending_action == "set_current"
        assert control.is_paused is True

    asyncio.run(run())
    assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1


@pytest.mark.parametrize("missing", [None, float("nan"), "invalid", "old_snapshot"])
@pytest.mark.parametrize("command", ["current", "unlimited", "recovery"])
def test_lost_or_old_readback_cannot_lift_a_confirmed_pause(missing, command):
    harness = Harness(error_code=200, values={"charging_current_ems": 8.0})

    async def run():
        control = await harness.connect()
        await control.async_pause_charging()
        if missing != "old_snapshot":
            harness.snapshot = WallboxData(values={"charging_current_ems": missing})
        harness.clock[0] += 1
        assert control.is_paused is True
        action = (control.async_recover_from_error() if command == "recovery" else
                  control.async_set_charging_current(
                      0 if command == "unlimited" else 8, allow_unlimited=True))
        if command == "recovery":
            with pytest.raises(ServiceValidationError) as error:
                await action
            assert error.value.translation_placeholders == {"seconds": "4"}
        else:
            await action
            assert control.pending_action == "set_current"
            assert control.is_paused is True
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        harness.clock[0] += 4
        await control.async_set_charging_current(8)
        assert control.is_paused is False

    asyncio.run(run())


def test_unknown_pause_state_keeps_the_startup_guard():
    harness = Harness(values={}, state_change_interval_seconds=300)

    async def run():
        control = await harness.connect()
        harness.client.write_state.mode_change_at = harness.clock[0]
        assert control.is_paused is None
        harness.clock[0] += 1
        await control.async_set_charging_current(8)
        assert control.pending_action == "set_current"
        assert not harness.transport.writes

    asyncio.run(run())


@pytest.mark.parametrize("rejection", ["device"])
def test_rejected_setpoint_does_not_change_the_later_resume(rejection: str) -> None:
    harness = Harness(values={"charging_current_ems": 8.0})

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 1 if rejection == "cooldown" else 6
        if rejection == "device":
            harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = 3
        with pytest.raises(HomeAssistantError):
            await control.async_set_charging_current(16)
        assert control.charging_setpoint == 8.0
        harness.transport.write_exceptions.clear()
        harness.clock[0] += 6
        await control.async_resume_charging()
        from custom_components.mennekes_amtron_rtu.device.decode import decode_register

        assert decode_register(
            R.CHARGING_CURRENT_EMS, harness.written(R.CHARGING_CURRENT_EMS)[-1]
        ) == 8.0

    asyncio.run(run())


def test_a_setpoint_change_undoes_a_pause_after_five_seconds() -> None:
    harness = Harness(values={"charging_current_ems": 16.0}, clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 5
        await control.async_set_charging_current(12)

    asyncio.run(run())
    from custom_components.mennekes_amtron_rtu.device.decode import decode_register

    writes = harness.written(R.CHARGING_CURRENT_EMS)
    assert len(writes) == 2
    assert decode_register(R.CHARGING_CURRENT_EMS, writes[-1]) == 12.0


def test_a_queued_mode_change_does_not_start_the_hysteresis_again() -> None:
    """A queued request must not push the deadline out; it never touched the bus."""

    harness = Harness(values={"charging_current_ems": 16.0}, clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 2
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        assert harness.client.write_state.mode_change_at == 1000
        harness.clock[0] += 3
        await control.async_resume_charging()

    asyncio.run(run())
    assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2


def test_the_five_second_floor_still_applies_to_a_pause() -> None:
    """A queued pause keeps the current register's own five-second floor."""

    harness = Harness(clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_charging_current(10)
        harness.clock[0] += 2
        harness.snapshot = WallboxData(values={"charging_current_ems": 10.0})
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        await asyncio.wait_for(control._flush_task, 1)
        assert harness.clock[0] == 1005
        assert control.is_paused is True

    asyncio.run(run())
    assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2


def test_the_unlimited_opt_in_cannot_undo_a_pause_early_either() -> None:
    """0 A makes the wallbox signal its maximum, so it is a resume too."""

    harness = Harness(values={"charging_current_ems": 16.0}, clock=[1000.0])

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.clock[0] += 1
        await control.async_set_charging_current(0, allow_unlimited=True)
        assert control.pending_action == "set_current"
        assert control.is_paused is True

    asyncio.run(run())
    assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1


@pytest.mark.parametrize("newer", ["pause", "setpoint", "unlimited"])
def test_an_accepted_command_supersedes_a_deferred_current(newer: str) -> None:
    """A stale slider write must never resume a later pause."""
    from custom_components.mennekes_amtron_rtu.device.decode import decode_register

    async def run() -> None:
        harness = Harness(values={"charging_current_ems": 6.0})
        control = await harness.connect()
        await control.async_set_charging_current(6)
        await control.async_set_charging_current(8)
        harness.clock[0] += 6
        if newer == "pause":
            await control.async_pause_charging()
        elif newer == "unlimited":
            await control.async_set_charging_current(0, allow_unlimited=True)
        else:
            await control.async_set_charging_current(16)
        for _ in range(20):
            await asyncio.sleep(0)
        assert [
            decode_register(R.CHARGING_CURRENT_EMS, words)
            for words in harness.written(R.CHARGING_CURRENT_EMS)
        ] == [6.0, {"pause": 1.0, "setpoint": 16.0, "unlimited": 0.0}[newer]]
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("initial", [0.0, 12.0])
def test_pause_and_resume_preserve_unlimited_mode(initial: float) -> None:
    async def run() -> None:
        harness = Harness(values={"charging_current_ems": initial}, current_cap=10)
        control = await harness.connect()
        control.charging_setpoint = 8  # An old limit must not replace unlimited.
        if initial != 0:
            await control.async_set_charging_current(0, allow_unlimited=True)
            assert control.charging_setpoint == 0
            harness.clock[0] += 5
        await control.async_pause_charging()
        assert control.charging_setpoint == 0
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        assert control.is_paused is True
        harness.clock[0] += 300
        await control.async_resume_charging()
        assert control.charging_setpoint == 0
        assert harness.written(R.CHARGING_CURRENT_EMS)[-1] == [0, 0]
        assert control.is_paused is False

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["lost_reply", "cancelled"])
def test_uncertain_pause_discards_an_older_queued_current(outcome):
    async def run():
        harness = Harness(values={"charging_current_ems": 6.0}, error_code=200)
        control = await harness.connect()
        await control.async_set_charging_current(6)
        await control.async_set_charging_current(8)
        flush = control._flush_task
        harness.clock[0] += 6
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = (
            asyncio.CancelledError() if outcome == "cancelled" else raise_connection_error()
        )
        with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else HomeAssistantError):
            await control.async_pause_charging()
        harness.transport.write_exceptions.clear()
        await flush
        assert control.pause_requested is True
        assert [decode_register(R.CHARGING_CURRENT_EMS, words) for words in harness.written(R.CHARGING_CURRENT_EMS)] == [6]
        harness.clock[0] += 6
        await control.async_recover_from_error()
        assert [decode_register(R.CHARGING_CURRENT_EMS, words) for words in harness.written(R.CHARGING_CURRENT_EMS)] == [6, 1]
        await control.async_shutdown()
        await harness.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["lost_reply", "cancelled"])
@pytest.mark.parametrize("followup", ["resume", "recovery"])
def test_uncertain_lower_current_discards_an_older_queued_increase(outcome, followup):
    """A lost ACK must not let an older slider target undo a lower limit."""

    async def run():
        harness = Harness(values={"charging_current_ems": 8.0}, error_code=200)
        control = await harness.connect()
        await control.async_set_charging_current(8)
        await control.async_set_charging_current(16)
        flush = control._flush_task
        harness.clock[0] += 6
        original = harness.transport.write_registers

        async def apply_without_confirmation(address, values, **kwargs):
            await original(address, values, **kwargs)
            if outcome == "cancelled":
                raise asyncio.CancelledError
            raise raise_connection_error()

        harness.transport.write_registers = apply_without_confirmation
        with pytest.raises(
            asyncio.CancelledError if outcome == "cancelled" else HomeAssistantError
        ):
            await control.async_set_charging_current(6)
        harness.transport.write_registers = original
        await flush
        assert [
            decode_register(R.CHARGING_CURRENT_EMS, words)
            for words in harness.written(R.CHARGING_CURRENT_EMS)
        ] == [8.0, 6.0]
        assert await harness.client.async_read_register(R.CHARGING_CURRENT_EMS) == 6.0
        assert control.charging_setpoint == 6.0
        assert control.is_paused is None
        harness.clock[0] += 5
        if followup == "resume":
            await control.async_pause_charging()
            assert control.charging_setpoint == 6.0
            harness.clock[0] += 5
            await control.async_resume_charging()
        else:
            await control.async_recover_from_error()
        assert control.charging_setpoint == 6.0
        assert decode_register(
            R.CHARGING_CURRENT_EMS, harness.written(R.CHARGING_CURRENT_EMS)[-1]
        ) == 6.0
        await control.async_shutdown()
        await harness.client.async_close()

    asyncio.run(run())


def test_deferred_zero_preserves_the_write_floor_and_replaces_a_pending_limit() -> None:
    async def run() -> None:
        harness = Harness()
        control = await harness.connect()
        await control.async_set_charging_current(10)
        await control.async_set_charging_current(8)
        await control.async_set_charging_current(0, allow_unlimited=True)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert control.charging_setpoint == 0
        await control._flush_task
        assert harness.clock[0] == 1005
        assert harness.written(R.CHARGING_CURRENT_EMS)[-1] == [0, 0]
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2

    asyncio.run(run())


def test_recovery_cannot_bypass_the_current_write_interval() -> None:
    async def run() -> None:
        harness = Harness(error_code=200)
        control = await harness.connect()
        await control.async_set_charging_current(6)
        harness.transport.bank[R.CHARGING_RELEASE.address] = 0
        harness.clock[0] += 4
        with pytest.raises(ServiceValidationError) as err:
            await control.async_recover_from_error()
        assert err.value.translation_key == "write_rate_limited"
        assert err.value.translation_placeholders == {"seconds": "1"}
        # A rejected recovery must not claim success or silently finish later.
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 1
        assert harness.written(R.HEARTBEAT) == []
        assert harness.written(R.CHARGING_RELEASE) == []
        assert harness.transport.bank[R.CHARGING_RELEASE.address] == 0
        assert harness.diagnostics.writes_rate_limited == 1
        assert harness.diagnostics.recoveries == 0
        await control.async_shutdown()
        assert [event["event"] for event in harness.diagnostics.timeline.snapshot()] == [
            "write_rate_limited",
        ]

    asyncio.run(run())


def test_a_sleeping_deferred_write_cannot_undo_a_pause() -> None:
    async def run() -> None:
        harness = Harness(values={"charging_current_ems": 6.0})
        entered = asyncio.Event()
        release = asyncio.Event()

        async def sleep(_delay: float) -> None:
            entered.set()
            await release.wait()

        control = await harness.connect()
        control._sleep = sleep
        await control.async_set_charging_current(6)
        await control.async_set_charging_current(8)
        await entered.wait()
        harness.clock[0] += 6
        await control.async_pause_charging()
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(harness.written(R.CHARGING_CURRENT_EMS)) == 2
        await control.async_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("charging", [False, True])
def test_phase_switch_rate_limit_queues_until_the_shared_interval(charging: bool) -> None:
    harness = Harness(values={"evse_state": EvseState.IDLE}, state_change_interval_seconds=300)

    async def run() -> None:
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        harness.clock[0] += 15
        if charging:
            harness.snapshot.values["evse_state"] = EvseState.CHARGING
            harness.transport.bank[R.EVSE_STATE.address] = int(EvseState.CHARGING)
        await control.async_set_requested_phases(RequestedPhases.ALL_AVAILABLE)
        assert control.pending_action == "all_phases"
        assert control._mode_change_remaining() == 285
        assert harness.diagnostics.writes_rate_limited == 1
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert harness.written(R.CHARGING_CURRENT_EMS) == []
        harness.clock[0] += 285
        await control.async_set_requested_phases(RequestedPhases.ALL_AVAILABLE)
        if charging:
            await harness.confirm_phase_pause()
        assert harness.written(R.REQUESTED_PHASES) == [[1], [0]]
        assert bool(harness.written(R.CHARGING_CURRENT_EMS)) == charging

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["lost_reply", "cancelled"])
def test_uncertain_pause_keeps_hysteresis_even_with_old_readback(outcome):
    harness = Harness(values={"charging_current_ems": 8.0})

    async def run():
        control = await harness.connect()
        error = asyncio.CancelledError() if outcome == "cancelled" else raise_connection_error()
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = error
        with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else HomeAssistantError):
            await control.async_pause_charging()
        harness.transport.write_exceptions.clear()
        harness.clock[0] += 1
        assert control.is_paused is None
        assert control.pause_requested is True
        await control.async_set_charging_current(8)
        assert control.pending_action == "set_current"
        assert control.is_paused is None
        assert not harness.written(R.CHARGING_CURRENT_EMS)

    asyncio.run(run())


@pytest.mark.parametrize(("cached", "actual", "pause"), [
    (EvseState.IDLE, EvseState.CHARGING, True),
    (None, EvseState.CHARGING, True),
    (EvseState.CHARGING, EvseState.IDLE, False),
])
def test_phase_switch_uses_fresh_device_status(cached, actual, pause):
    harness = Harness(values={"charging_current_ems": 8.0})
    harness.snapshot.values["evse_state"] = cached
    harness.transport.bank[R.EVSE_STATE.address] = int(actual)

    async def run():
        control = await harness.connect()
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert bool(harness.written(R.CHARGING_CURRENT_EMS)) == pause
        if pause:
            await harness.confirm_phase_pause()
        assert harness.written(R.REQUESTED_PHASES) == [[1]]
        assert (R.EVSE_STATE.address, 1) in harness.transport.reads

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["phase", "restart"])
@pytest.mark.parametrize("failure", ["missing", "protocol", "invalid"])
def test_guarded_operations_refuse_unconfirmed_status_without_writes(operation, failure):
    harness = Harness(values={"evse_state": EvseState.IDLE})
    if failure == "invalid":
        harness.transport.bank[R.EVSE_STATE.address] = 99
    else:
        harness.transport.read_exceptions[R.EVSE_STATE.address] = (
            raise_connection_error() if failure == "missing" else 4
        )

    async def run():
        control = await harness.connect()
        action = (control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
                  if operation == "phase" else control.async_restart())
        with pytest.raises(ServiceValidationError) as error:
            await action
        assert error.value.translation_key == f"{operation}_state_unknown"
        assert not harness.transport.writes

    asyncio.run(run())


@pytest.mark.parametrize(("cached", "actual"), [
    (EvseState.IDLE, EvseState.CHARGING),
    (EvseState.IDLE, EvseState.EV_CONNECTED),
    (EvseState.CHARGING, EvseState.IDLE),
    (None, EvseState.IDLE),
])
def test_restart_uses_fresh_device_status(cached, actual):
    harness = Harness(values={"evse_state": EvseState.IDLE})
    harness.snapshot.values["evse_state"] = cached
    harness.transport.bank[R.EVSE_STATE.address] = int(actual)

    async def run():
        control = await harness.connect()
        if actual == EvseState.IDLE:
            await control.async_restart()
        else:
            with pytest.raises(ServiceValidationError) as error:
                await control.async_restart()
            assert error.value.translation_key == "restart_requires_idle"
        assert bool(harness.written(R.SYSTEM_RESTART)) == (actual == EvseState.IDLE)
        assert harness.transport.reads == [(R.EVSE_STATE.address, 1)]

    asyncio.run(run())


@pytest.mark.parametrize("previous", [0.0, 8.0])
@pytest.mark.parametrize("outcome", ["lost_reply", "cancelled"])
def test_an_uncertain_pause_retains_the_actual_resume_current(previous, outcome):
    async def run():
        harness = Harness(values={"charging_current_ems": previous})
        control = await harness.connect()
        assert control.charging_setpoint is None
        original = harness.transport.write_registers

        async def apply_without_confirmation(address, values, **kwargs):
            await original(address, values, **kwargs)
            if outcome == "cancelled":
                raise asyncio.CancelledError
            raise raise_connection_error()

        harness.transport.write_registers = apply_without_confirmation
        with pytest.raises(
            asyncio.CancelledError if outcome == "cancelled" else HomeAssistantError
        ):
            await control.async_pause_charging()
        assert control.pause_requested is True
        assert control.charging_setpoint == previous
        harness.transport.write_registers = original
        harness.snapshot = WallboxData(
            values={"charging_current_ems": 1.0},
            current_write_generation=harness.client.write_state.current_write_generation,
        )
        harness.clock[0] += 5
        await control.async_resume_charging()
        assert [
            decode_register(R.CHARGING_CURRENT_EMS, words)
            for words in harness.written(R.CHARGING_CURRENT_EMS)
        ] == [1.0, previous]
        await control.async_shutdown()
        await harness.client.async_close()

    asyncio.run(run())


def test_uncertain_paused_recovery_retains_intent_over_fallback_current():
    async def run():
        harness = Harness(values={"charging_current_ems": 16.0}, error_code=200)
        control = await harness.connect()
        control.pause_requested = True
        control.charging_setpoint = 8.0
        harness.transport.write_exceptions[R.CHARGING_CURRENT_EMS.address] = (
            raise_connection_error()
        )
        with pytest.raises(HomeAssistantError):
            await control.async_recover_from_error()
        assert control.pause_requested is True
        assert control.charging_setpoint == 8.0
        await control.async_shutdown()
        await harness.client.async_close()

    asyncio.run(run())
