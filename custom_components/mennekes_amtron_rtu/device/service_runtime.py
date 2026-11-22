"""Own the Modbus connection and control lifetime independently of API clients."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from time import time as wall_clock
from typing import Any

from .charge_meter_store import ChargeMeterStore
from .client import MennekesModbusClient
from .client_errors import AmtronBusError
from .command_events import SOURCES, attributed, command_source
from .commands import COMMANDS, ENVELOPE, needs_wallbox
from .connection_sample import connection_sample
from .const import (
    CHARGING_CURRENT_MINIMUM,
    ERROR_CODE_EMS_UNAVAILABLE,
    charging_setpoint_from,
)
from .control import AmtronControl, ControlOptions
from .data import ConnectionState, DeviceIdentity, WallboxData, WriteDiagnostics
from .departure_plan import DepartureController
from .departure_planner import DeparturePlanner
from .energy_goal import EnergyGoal
from .energy_goal_controller import EnergyGoalController
from .enums import EvseState
from .errors import DeviceError, DeviceValidationError
from .guest_limits import GuestLimits
from .heartbeat import HeartbeatTask
from .identity import async_read_identity
from .meter_log import ChargeMeter, moment
from .notice import FailureNotice
from .operation import summarize
from .persistence import (
    StorageCommitUncertainError,
    StorageStateUnknownError,
    finish_goal_save,
)
from .plug_cycle import PlugChange
from .poller import DevicePoller
from .register_blocks import REGISTER_BLOCKS
from .register_monitor import RegisterMonitor
from .registers import ENERGY_TOTAL, ERROR_CODE, EVSE_STATE, REGISTERS_BY_KEY
from .service_config import ServerConfig, number
from .service_store import IntentStore
from .service_version import SERVICE_VERSION
from .soc_choice import SocChoice
from .soc_goal import SocGoal
from .soc_goal_controller import SocGoalController
from .soc_transactions import SocTransactions
from .write_state import WriteState

_LOGGER = logging.getLogger(__name__)
API_VERSION = 1
# A state poll waits this long for a running command, then takes the last
# published state unless that is older than the second value.
_PATIENCE_SECONDS = 2.0
_METER_SAVE_SECONDS = 300.0
_METER_RETRY_SECONDS = 60.0
# A kWh goal pauses for a missing meter only after the bus failed this long.
METER_GRACE_SECONDS = 60.0
_PUBLISHED_SECONDS = 60.0


class ModbusService:
    """All stateful operations remain here when HA disconnects or restarts."""

    def __init__(
        self,
        config: ServerConfig,
        state_path: Path,
        *,
        client: MennekesModbusClient | None = None,
    ) -> None:
        self.config = config
        self._connection_name = {
            "serial": "serial",
            "rtu_over_tcp": "RTU-over-TCP",
            "modbus_tcp": "Modbus TCP",
        }[config.connection.connection_type]
        self.client = client or MennekesModbusClient(
            config.connection, write_state=WriteState.cold_start(time.monotonic())
        )
        self.state_path = state_path
        self.identity = DeviceIdentity()
        self.data = WallboxData()
        self.diagnostics = WriteDiagnostics()
        self.connection = ConnectionState(available=False)
        # Why the newest poll failed; None while the wallbox answers.
        self.failure: Exception | None = None
        self.heartbeat = HeartbeatTask(self.client, self.diagnostics)
        # Passive observations for the app's register page.
        self.register_monitor = RegisterMonitor()
        # What the meter delivered in any time span, for cost and loss estimates.
        self.charge_meter = ChargeMeter()
        self._meter_store: ChargeMeterStore | None = None
        # What of the plug cycle was last saved, when, and at which meter value.
        self._meter_saved: tuple[object, float, float | None] | None = None
        self._meter_lock = asyncio.Lock()
        self._meter_retry_at = 0.0
        # The count of writes the last poll after a write has read back: a
        # command whose last write is among them needs no second poll.
        self._read_back: int | None = None
        self._meter_notice = FailureNotice(_LOGGER, "Charging loss")
        self.client.modbus_trace.register_monitor = self.register_monitor
        self.control: AmtronControl | None = None
        # Guest permission and departure plan: parts of this one service.
        self.guest = GuestLimits(self)
        self.planner = DeparturePlanner(self)
        self.goal: EnergyGoalController | None = None
        self.soc_goal: SocGoalController | None = None
        self._poller: DevicePoller | None = None
        self._store: IntentStore | None = None
        self._poll_lock = asyncio.Lock()
        self._store_lock = asyncio.Lock()
        self._wallbox_notice = FailureNotice(_LOGGER, "Connection")
        self._intent_notice = FailureNotice(_LOGGER, "Control")
        self._rejected_notice = FailureNotice(_LOGGER, "Control")
        self._listener_notice = FailureNotice(_LOGGER, "Service")
        self.command_lock = asyncio.Lock()
        # The last state published after a completed command, and when.
        self.published: tuple[float, dict[str, Any]] | None = None
        # A fresh boot changes the revision, so a client cannot mistake an old
        # cached command outcome for a goal restored after a service restart.
        self.soc_choice = SocChoice(self)
        self.soc_transactions = SocTransactions(self)
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_started = asyncio.Event()
        self._success = False
        self._plug_observed = False
        self._attempts = 0
        self._retry_at = 0.0
        self._cap_retry_at = 0.0
        self._updated_at: float | None = None
        self.energy_sample: dict[str, float | None] | None = None
        # When the sample was taken, on the monotonic clock.
        self.energy_sample_at: float | None = None
        # Since when every poll failed; a goal pauses only after a while.
        self._meter_failing_since: float | None = None
        self._cap_notice = FailureNotice(_LOGGER, "Control")
        self._intent_rejected = False
        self.stopping = False
        # Set by the first attempt to reach the wallbox, whatever its outcome.
        self._attempted = asyncio.Event()
        self._startup_error: BaseException | None = None
        self._phase = f"opening {self._connection_name} connection"
        # Called after every poll, failed or not: whoever shows the state
        # need not guess when it changed.
        self.on_polled: Callable[[], None] | None = None
        self.heartbeat.async_add_listener(self._log_heartbeat_health)

    def start(self) -> asyncio.Task[None]:
        _LOGGER.info("Connection: opening %s connection to the wallbox", self._connection_name)
        self._task = asyncio.create_task(self._run(), name="amtron_service")
        return self._task

    async def wait_started(self) -> None:
        """Return once the wallbox is initialized; raise what the first try raised.

        The service keeps retrying on its own. An owner that needs an answer
        now, like a Home Assistant setup, decides here whether to keep it.
        """
        task = self._task
        if task is None:
            raise RuntimeError("service was not started")
        attempted = asyncio.create_task(self._attempted.wait())
        try:
            await asyncio.wait((task, attempted), return_when=asyncio.FIRST_COMPLETED)
        finally:
            attempted.cancel()
        if task.done():
            if task.cancelled():
                # Stopped by its owner while it started; the waiting one was not.
                raise DeviceError("service_unavailable")
            error = task.exception()
            # A task group wraps what ended it; the owner decides by the cause.
            while isinstance(error, BaseExceptionGroup) and error.exceptions:
                error = error.exceptions[0]
            if error is not None:
                raise error
            raise RuntimeError("service stopped")
        if self._startup_error is not None:
            raise self._startup_error

    def _log_heartbeat_health(self) -> None:
        if self.stopping:
            return
        if self.heartbeat.healthy is True:
            _LOGGER.info("Heartbeat: acknowledged by the wallbox")
        elif self.heartbeat.healthy is False:
            _LOGGER.warning(
                "Heartbeat: acknowledgement missing; checking the %s connection",
                self._connection_name,
            )

    async def _initialize(self) -> None:
        self.register_monitor.clear()
        self._phase = f"opening {self._connection_name} connection"
        await self.client.async_connect()
        self._phase = "reading wallbox identity"
        identity = await async_read_identity(self.client)
        store = IntentStore(
            self.state_path,
            identity.serial_number
            or f"{self.config.connection.port}:{self.config.connection.device_id}",
        )
        if self._meter_store is None:
            self._phase = "loading saved charge meter"
            await self._load_charge_meter(store.identity)
        self._phase = "loading saved control intent"
        self._intent_rejected = not await asyncio.to_thread(store.load, self.client.write_state)
        if self._intent_rejected:
            # Do not migrate or overwrite rejected data. Hold the physical gate
            # until an administrator acknowledges a fresh, paused configuration.
            state = self.client.write_state
            safe = WriteState(pause_requested=True, charging_release_requested=False, charging_setpoint=6)
            # Preserve write guards and generations across reconnect attempts.
            state.pause_requested = safe.pause_requested
            state.charging_release_requested = safe.charging_release_requested
            state.charging_setpoint = safe.charging_setpoint
            state.guest_permission = None
            state.energy_goal = safe.energy_goal
            state.soc_goal = safe.soc_goal
            state.plug_charge = safe.plug_charge
            state.plug_charge.configure(True, "0" * 64)
            state.plug_charge.hold = True
            # Every reconnect attempt loads it again.
            self._rejected_notice.failed(
                "saved intent rejected; charging blocked until acknowledged in Home Assistant Repairs",
                "intent_rejected",
            )
        self.identity = identity
        self._store = store
        self.config = self.resolved_config(self.config)
        self._poller = DevicePoller(
            self.client,
            identity,
            connection_state=self.connection,
            measurement_interval=self.config.measurement_interval,
            timeline=self.diagnostics.timeline,
        )
        control = AmtronControl(
            self.client,
            diagnostics=self.diagnostics,
            identity=identity,
            options=ControlOptions(
                self.config.read_only,
                self.config.current_cap,
                state_change_interval_seconds=self.config.state_change_interval_seconds,
                phase_switch_grace_seconds=self.config.phase_switch_grace_seconds,
            ),
            state=lambda: self.data,
            request_refresh=self._control_updated,
            validate_deferred=self._validate_deferred,
        )
        self.guest.configure_control(control)
        self.planner.configure_control(control)
        goal = EnergyGoalController(control, self.client, self.persist)
        soc_goal = SocGoalController(control, self.persist)
        try:
            await control.async_sync_plug_charge()
            await self.refresh()
            control.follow_plug(PlugChange(), self.data)
            await control.async_sync_plug_charge()
        except BaseException:
            await control.async_shutdown()
            raise
        control.startup_hold = self._intent_rejected
        # With no saved resume limit, a restored pause must not default to the
        # maximum available current. Only an explicit command may resume it.
        if control.charging_setpoint is None:
            current = self.data.get("charging_current_ems")
            if isinstance(current, (int, float)):
                # A pause found on the wallbox resumes at the least current.
                found = charging_setpoint_from(float(current))
                control.charging_setpoint = CHARGING_CURRENT_MINIMUM if found is None else found
        await self.save_intent()
        self.control = control
        self.goal = goal
        self.soc_goal = soc_goal
        try:
            # Guards load before goal recovery and heartbeat startup.
            await self.planner.initialize()
            await self.guest.initialize()
        except BaseException:
            # Half an initialization would poll without ever starting the
            # heartbeat. The next attempt starts over.
            self.control = self.goal = self.soc_goal = None
            await goal.async_stop()
            await soc_goal.async_stop()
            await control.async_shutdown()
            raise
        goal.observe(self.data)
        soc_goal.retry_stop()
        _LOGGER.info("Connection: %s connection established; wallbox initialized", self._connection_name)
        if not self.config.read_only:
            self._heartbeat_task = self.heartbeat.start()
            self._heartbeat_started.set()
            _LOGGER.info("Heartbeat: started")
        else:
            _LOGGER.info("Heartbeat: disabled in read-only mode")

    @property
    def departure(self) -> DepartureController | None:
        return self.planner.controller

    @property
    def guest_deadline(self) -> datetime | None:
        """The shared target time also limits a guest."""
        return self.planner.deadline

    @property
    def target_time_revision(self) -> str | None:
        return self.planner.revision

    async def _run(self) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._run_bus(), name="amtron_bus_owner")
                group.create_task(self.guest.loop(), name="amtron_guest_limits")
                group.create_task(self.planner.loop(), name="amtron_departure")
        except BaseExceptionGroup as failed:
            # The part that failed ends the service with its own error, as
            # an owner and the process exit expect it.
            cause: BaseException = failed
            while isinstance(cause, BaseExceptionGroup):
                cause = cause.exceptions[0]
            raise cause from None

    async def _run_bus(self) -> None:
        await self._run_polling()
        raise RuntimeError("Modbus bus owner stopped unexpectedly")

    async def _run_polling(self) -> None:
        """Fail the service if its independent heartbeat writer exits."""
        if self.config.read_only:
            await self._polling_loop()
            return
        polling = asyncio.create_task(self._polling_loop(), name="amtron_polling")
        ready = asyncio.create_task(self._heartbeat_started.wait())
        try:
            done, _pending = await asyncio.wait(
                (polling, ready), return_when=asyncio.FIRST_COMPLETED
            )
            if polling in done:
                await polling
                return
            heartbeat = self._heartbeat_task
            if heartbeat is None:
                raise RuntimeError("Heartbeat task was not started")
            done, _pending = await asyncio.wait(
                (polling, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                self._success = False
                reason = "cancelled" if heartbeat.cancelled() else "normal return"
                if not heartbeat.cancelled() and (error := heartbeat.exception()) is not None:
                    reason = type(error).__name__
                _LOGGER.error("Heartbeat: task stopped unexpectedly (%s)", reason)
                raise RuntimeError("Heartbeat task stopped unexpectedly")
            await polling
        finally:
            # The owner stops the heartbeat separately, after the polling task
            # has finished any shielded state save. Never orphan a poller.
            for task in (polling, ready):
                task.cancel()
            await asyncio.gather(polling, ready, return_exceptions=True)

    async def _polling_loop(self) -> None:
        while True:
            # Whether a poll of this round told the plug cycle what it saw.
            self._plug_observed = False
            try:
                if self.control is None:
                    await self._initialize()
                else:
                    await self.refresh()
                await self._recover()
                await self._enforce_current_cap()
                await self._close_release_after_unplug()
                await self.save_intent()
                self._wallbox_notice.recovered("wallbox communication restored")
                self._startup_error = None
                self.failure = None
            except (AmtronBusError, DeviceError, OSError, ValueError) as err:
                self._success = False
                self.failure = err
                if self.control is None:
                    self._startup_error = err
                if not self._plug_observed:
                    # Nothing was read this round: the car is unknown, not
                    # present. A poll that read it keeps what it read.
                    self._observe_plug(None)
                unreadable = self._phase == "loading saved control intent" and not isinstance(err, AmtronBusError)
                self._wallbox_notice.failed(
                    ("saved intent unreadable, check the file's owner and mode" if unreadable
                     else f"wallbox unavailable during {self._phase}")
                    + f"; retrying every {self.config.scan_interval:.0f} seconds",
                    type(err).__name__,
                )
            self._attempted.set()
            if self.on_polled is not None:
                try:
                    self.on_polled()
                except Exception as err:  # noqa: BLE001 - a listener never stops the bus owner
                    # Whoever shows the state never stops the bus owner.
                    self._listener_notice.failed(
                        "poll listener failed", type(err).__name__,
                        level=logging.ERROR, exc_info=True,
                    )
                else:
                    self._listener_notice.recovered("poll listener works again")
            await asyncio.sleep(self.config.scan_interval)

    async def save_intent(self) -> None:
        """A disk that fails is not a lost wallbox: the state stays live."""
        try:
            await self.persist()
        except (OSError, ValueError) as err:
            self._intent_notice.failed("intent not saved; the wallbox stays in use", type(err).__name__)
        else:
            self._intent_notice.recovered("intent saved again")

    async def refresh(self) -> None:
        try:
            await self._poll()
        finally:
            try:
                self.soc_choice.follow()
                self.guest.follow()
            finally:
                if self.departure is not None:
                    self.departure.observe()
        await self._save_charge_meter()

    async def _load_charge_meter(self, identity: str) -> None:
        """Once per service life: go on measuring where the last run stopped."""
        store = ChargeMeterStore(self.state_path.with_name("charge-meter.json"), identity)
        try:
            saved = await asyncio.to_thread(store.load)
            if saved is not None:
                self.charge_meter.restore(saved)
        except (OSError, ValueError) as err:
            # An estimate only: measuring starts again, charging is unaffected.
            _LOGGER.warning("Charging loss: saved meter log unusable; starting without it (%s)", type(err).__name__)
        self._meter_store = store

    async def _save_charge_meter(self, *, force: bool = False) -> None:
        """Save at every change of the plug cycle, and every five minutes while the meter rises."""
        store, meter = self._meter_store, self.charge_meter
        if store is None:
            return
        cycle = (meter.present, meter.plugged_at, meter.charging_at, meter.note, meter.last, meter.last_note)
        saved = self._meter_saved
        now = time.monotonic()
        if not force and (
            now < self._meter_retry_at
            or (saved is not None and saved[0] == cycle and (
                # Without a car charging nothing is worth a write.
                now - saved[1] < _METER_SAVE_SECONDS or meter.log.last_kwh == saved[2]
            ))
        ):
            return
        # Shielded and one at a time: the write of a cancelled poll must not
        # overtake the one at stop and leave the older state on disk.
        await asyncio.shield(self._write_charge_meter(store, (cycle, now, meter.log.last_kwh), meter.dump()))

    async def _write_charge_meter(
        self, store: ChargeMeterStore, saved: tuple[object, float, float | None], payload: dict[str, Any],
    ) -> None:
        async with self._meter_lock:
            try:
                await asyncio.to_thread(store.save, payload)
            except (OSError, ValueError) as err:
                # Not recorded as saved: tried again in a minute.
                self._meter_retry_at = saved[1] + _METER_RETRY_SECONDS
                self._meter_notice.failed("meter log not saved", type(err).__name__)
            else:
                self._meter_saved = saved
                self._meter_notice.recovered("meter log saved again")

    async def acknowledge_intent_reset(self, control: AmtronControl) -> None:
        """Take over safe defaults for a saved intent that could not be read."""
        if not self._intent_rejected:
            return
        if self._store is None:
            raise DeviceError("service_unavailable")
        safe = WriteState(pause_requested=True, charging_release_requested=False, charging_setpoint=6)
        async with self._store_lock:
            await finish_goal_save(asyncio.to_thread(self._store.save, safe))
        # Vehicle absence can clear the temporary gate's release intent.
        # Restore the durable safe intent without replacing the live state
        # and its Modbus write limiter.
        state = self.client.write_state
        state.pause_requested = safe.pause_requested
        state.charging_release_requested = safe.charging_release_requested
        state.charging_setpoint = safe.charging_setpoint
        state.plug_charge = safe.plug_charge
        state.guest_permission = None
        self._intent_rejected = False
        self._rejected_notice.recovered("saved intent reset acknowledged")
        control.startup_hold = False

    async def charge_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Home Assistant names the car of the running plug cycle; no bus access."""
        if payload.keys() != COMMANDS["charge_note"].fields | ENVELOPE:
            raise ValueError("invalid charge note")
        try:
            plugged_at = moment(payload["plugged_at"])
        except ValueError:
            raise ValueError("invalid charge note") from None
        if self.stopping:
            raise DeviceError("service_unavailable")
        if payload["expected_serial_number"] != self.identity.serial_number:
            raise DeviceError("device_identity_changed")
        # A cycle that ended meanwhile keeps its note; the answer shows it.
        note = self.charge_meter.note
        if self.charge_meter.annotate(plugged_at, payload["note"]) and self.charge_meter.note != note:
            await self._save_charge_meter(force=True)
        if self.command_lock.locked():
            # Another command still runs; its outcome is not this one's. What
            # is published stays, so readers need not wait for that command.
            return self.snapshot()
        return self.publish()

    def _observe_plug(self, data: WallboxData | None, *, observed_at: datetime | None = None) -> None:
        """One poll, or None for a failed one, for the plug cycle and the rules that follow it."""
        if self.control is not None:
            self.control.observe_plug(data, observed_at=observed_at)
        else:
            # The first poll of a control that is not in use yet.
            self.client.write_state.plug_cycle.read(data, self.identity.layout_version, at=observed_at)

    async def _poll(self) -> None:
        async with self._poll_lock:
            self._phase = "polling the wallbox"
            if self._poller is None:
                raise DeviceError("service_unavailable")
            goal_generation = self.client.write_state.energy_goal_generation
            observed_at = datetime.now(UTC)
            # Either way below, this poll tells the plug cycle itself.
            self._plug_observed = True
            try:
                self.data = await self._poller.async_poll(
                    self.data, last_success=self._success
                )
            except AmtronBusError:
                self._success = False
                self._observe_plug(None)
                self.energy_sample = self.energy_sample_at = None
                self.data.energy_observed_at = None
                self.charge_meter.observe(wall_clock(), present=None)
                now = time.monotonic()
                if self._meter_failing_since is None:
                    self._meter_failing_since = now
                if self.goal is not None and now - self._meter_failing_since >= METER_GRACE_SECONDS:
                    # One lost poll is no outage: pausing for it and resuming
                    # five minutes later would cost more than it protects.
                    self.goal.observe(WallboxData(
                        energy_read=True, energy_goal_generation=goal_generation,
                    ))
                raise
            self._meter_failing_since = None
            self._success = True
            self._updated_at = time.monotonic()
            if self.data.energy_read:
                energy = self.data.get(ENERGY_TOTAL.key)
                # Keep the timestamp of a failed meter attempt as well. A
                # cached value must never turn a measurement gap into energy.
                self.energy_sample_at = time.monotonic()
                self.energy_sample = {
                    "observed_at": wall_clock(),
                    "value": (
                        float(energy) if isinstance(energy, (int, float)) and not isinstance(energy, bool)
                        and math.isfinite(energy) and energy >= 0 else None
                    ),
                }
            self.data.energy_observed_at = (
                self.energy_sample["observed_at"] if self.energy_sample is not None else None
            )
            sample = self.energy_sample if self.data.energy_read else None
            self._observe_plug(self.data, observed_at=observed_at)
            self.charge_meter.observe(
                (sample["observed_at"] if sample is not None else None) or wall_clock(),
                present=self.client.write_state.plug_cycle.metered,
                charging=self.data.get(EVSE_STATE.key) == EvseState.CHARGING,
                reading=sample["value"] if sample is not None else None,
            )
            if self.control is not None:
                self.control.observe_recovery(
                    self.data.fresh("error_code"), self.data.current_write_generation,
                )
            if self.goal is not None:
                self.goal.observe(self.data)
            if self.soc_goal is not None:
                self.soc_goal.retry_stop()

    @attributed("recovery")
    async def _recover(self) -> None:
        self._phase = "recovering the wallbox error"
        if self.data.get("error_code") == 0:
            self._attempts = 0
        if (
            self.control is None
            or self.control.read_only
            or self.heartbeat.healthy is not True
            or self.data.get("error_code") != ERROR_CODE_EMS_UNAVAILABLE
            or self._attempts >= 3
            or time.monotonic() < self._retry_at
        ):
            return
        try:
            async with self.command_lock:
                attempted = await self.control.async_recover_from_error()
            self._attempts += int(attempted)
            self._retry_at = time.monotonic() + 30
            await self.refresh()
        except DeviceValidationError as err:
            if err.translation_key == "write_rate_limited":
                self._retry_at = time.monotonic() + 5
            else:
                self._attempts += 1
                self._retry_at = time.monotonic() + 30
        except DeviceError, AmtronBusError:
            self._attempts += 1
            self._retry_at = time.monotonic() + 30
        finally:
            await self.save_intent()

    @attributed("unplug")
    async def _close_release_after_unplug(self) -> None:
        if self.control is not None:
            self._phase = "closing the release after an unplug"
            async with self.command_lock:
                await self.control.unplug_release.async_apply()
                await self.control.unplug_release.async_end_reached_goal()

    async def _enforce_current_cap(self) -> None:
        """Never leave a confirmed charging current above a lowered cap."""
        control = self.control
        if control is None or control.read_only or time.monotonic() < self._cap_retry_at:
            return
        if self.config.cap_from_wallbox and self.identity.max_evse_current is None:
            # The wallbox cannot say its maximum and nobody set a cap: the
            # placeholder bounds new requests but lowers no running charge.
            return
        self._phase = "applying the current cap"
        try:
            async with self.command_lock:
                lowered = await control.async_enforce_current_cap()
        except (DeviceError, AmtronBusError) as err:
            self._cap_retry_at = time.monotonic() + 30
            self._cap_notice.failed(
                f"charging current above the {control.max_charging_current:.1f} A cap not lowered;"
                " retrying every 30 seconds",
                err.translation_key if isinstance(err, DeviceError) else type(err).__name__,
            )
            return
        self._cap_notice.recovered("charging current cap enforced again")
        if lowered:
            _LOGGER.info("Control: charging current lowered to the %.1f A cap", control.max_charging_current)

    async def persist(self) -> None:
        self.guest.stamp_permission()
        if self._store is not None and not self._intent_rejected:
            async with self._store_lock:
                self._phase = "saving control intent"
                original = self.client.write_state
                state = WriteState(
                    guest_permission=original.guest_permission,
                    charging_setpoint=original.charging_setpoint,
                    pause_requested=original.pause_requested,
                    charging_release_requested=original.charging_release_requested,
                    energy_goal=EnergyGoal.restore(original.energy_goal.snapshot()),
                    soc_goal=SocGoal.restore(original.soc_goal.snapshot()),
                    plug_charge=replace(original.plug_charge, authorized=False),
                    unplug_release=replace(original.unplug_release),
                )
                saving = asyncio.create_task(asyncio.to_thread(self._store.save, state))
                cancelled = False
                # The worker thread cannot be cancelled. Keep the store lock
                # until it completes, even if shutdown or the HTTP handler is
                # cancelled again; otherwise an older save can overwrite a
                # newer intent after that newer save has already returned.
                while not saving.done():
                    try:
                        await asyncio.shield(saving)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:  # noqa: BLE001 - raised from the task below
                        break
                if cancelled:
                    # Cancellation outranks a save that failed meanwhile.
                    raise asyncio.CancelledError from saving.exception()
                saving.result()

    async def _control_updated(self) -> None:
        """Publish queue changes and persist commands executed by the worker."""
        # A command may already have reached the wallbox. Save its intent
        # before a poll can fail or be cancelled during a connection outage.
        # A disk that refuses must not break off what the wallbox is in the
        # middle of, such as its recovery; a command reports it at its end.
        await self.save_intent()
        written = self.diagnostics.writes_sent
        await self.refresh()
        # A poll that carried a block forward has not read the write back.
        fresh = not self.data.held and not self.data.failed_blocks
        self._read_back = written if fresh else None

    def _validate_deferred(self) -> None:
        """A queued command needs the same fresh connection as an API request."""
        if self.stopping or not self.is_available():
            raise DeviceError("service_unavailable")

    def is_available(self) -> bool:
        """Share the freshness boundary for state publication and command admission."""
        return (
            self._success
            and self.client.identity_verified
            and self._updated_at is not None
            and time.monotonic() - self._updated_at
            <= max(30, self.config.scan_interval * 3)
        )

    def snapshot(self) -> dict[str, Any]:
        result = self._device_snapshot()
        departure = self.departure
        result["control"] = {
            **result["control"],
            # None while the plan is loading; Home Assistant then waits
            # instead of planning on its own.
            "departure": departure.snapshot() if departure is not None and departure.ready else None,
            "target_time_revision": self.target_time_revision,
            "guest_revision": self.guest.revision,
            "guest_active": self.guest.active,
            "guest_mode": self.guest.mode,
            "guest_confirmation_required": self.guest.restore_pending,
        }
        # Who controls the charge, why it waits and what comes next.
        result["control"]["operation"] = summarize(
            available=result["available"] is True, present=connection_sample(self).present,
            control=result["control"], departure=result["control"]["departure"],
            # Shared energy, percentage and guest-time stops report a reached target.
            goal_stop=self.control is not None and self.control.energy_goal_stop_requested,
        )
        return result

    def _device_snapshot(self) -> dict[str, Any]:
        control = self.control
        return {
            "api_version": API_VERSION,
            "service_version": SERVICE_VERSION,
            "identity": asdict(self.identity),
            "available": self.is_available(),
            "values": self.data.values,
            "charging_display": self.data.charging_display,
            "failed_blocks": list(self.data.failed_blocks),
            "current_write_generation": self.data.current_write_generation,
            "connection_quality": self.data.connection_quality,
            "connection_failures": self.data.connection_failures,
            "connection_retries": self.data.connection_retries,
            "energy_sample": dict(self.energy_sample) if self.energy_sample is not None else None,
            "charge_meter": self.charge_meter.snapshot(),
            "command_events": control.events.snapshot() if control else None,
            "control": {
                "plug_charge": self.client.write_state.plug_charge.snapshot(self.client.write_state.plug_cycle),
                # Manual permission is distinct from automatic vehicle authorization.
                "plug_charge_manual": self.client.write_state.plug_charge.manual,
                "release_off_on_unplug": self.client.write_state.unplug_release.enabled,
                "supports_energy_goal": control.supports_energy_goal if control else False,
                "supports_soc_goal": control.supports_soc_goal if control else False,
                "soc_goal_revision": self.soc_choice.revision,
                "soc_maximum_required": self.soc_choice.maximum_required,
                "soc_goal": control.soc_goal.snapshot() if control else None,
                "energy_goal": control.energy_goal.snapshot() if control else None,
                "cost_price": (
                    control.goal_controller.price
                    if control is not None and control.goal_controller is not None else None
                ),
                "cost_currency": (
                    control.goal_controller.currency
                    if control is not None and control.goal_controller is not None else None
                ),
                "read_only": self.config.read_only or self._intent_rejected,
                "intent_rejected": self._intent_rejected,
                "current_cap": self.config.current_cap,
                "state_change_interval_seconds": self.config.state_change_interval_seconds,
                "phase_switch_grace_seconds": self.config.phase_switch_grace_seconds,
                "pending_action": control.pending_action if control else None,
                "pending_until": (
                    control.pending_until.isoformat()
                    if control and control.pending_until is not None else None
                ),
                "max_charging_current": control.max_charging_current if control else 6,
                "charging_setpoint": control.charging_setpoint if control else None,
                "pause_requested": control.pause_requested if control else None,
                "charging_release_requested": control.charging_release_requested
                if control
                else None,
            },
            "heartbeat": {
                "running": self.heartbeat.running,
                "healthy": self.heartbeat.healthy,
            },
            "diagnostics": {
                key: getattr(self.diagnostics, key)
                for key in (
                    "heartbeats_sent",
                    "heartbeats_failed",
                    "heartbeats_late",
                    "writes_sent",
                    "writes_rejected",
                    "writes_rate_limited",
                    "recoveries",
                )
            },
            "write_errors": sorted(self.diagnostics.write_errors),
        }

    async def apply_runtime_settings(self, candidate: ServerConfig) -> None:
        """Keep the bus owner, heartbeat, hold/backoff state and pending commands.

        A running poll or sleep is not interrupted; the next ordinary cycle
        uses the new intervals. New commands use the new limits at once, and
        the polling loop lowers a charging current above a lowered cap.
        """
        control = self.control
        if self.stopping or control is None:
            raise DeviceError("service_unavailable")
        candidate = self.resolved_config(candidate)
        cap = number(candidate.current_cap, 6, 32)
        options = ControlOptions(
            control.options.read_only, cap,
            state_change_interval_seconds=candidate.state_change_interval_seconds,
            phase_switch_grace_seconds=candidate.phase_switch_grace_seconds,
        )
        measurement = number(candidate.measurement_interval, 2, 300)
        scan = number(candidate.scan_interval, 2, 300)
        # The only wait. A command takes the control lock before it refreshes,
        # so this one never waits for the poll lock while holding it, and a
        # timeout here leaves nothing half applied.
        await control.async_set_limits(options)
        if self._poller is not None:
            self._poller.update_measurement_interval(measurement)
        self.config = replace(
            self.config, scan_interval=scan, measurement_interval=measurement,
            current_cap=cap, cap_from_wallbox=candidate.cap_from_wallbox,
            state_change_interval_seconds=options.state_change_interval_seconds,
            phase_switch_grace_seconds=options.phase_switch_grace_seconds,
        )

    def resolved_config(self, config: ServerConfig) -> ServerConfig:
        """Settings with the wallbox's maximum as the cap where asked for."""
        if not config.cap_from_wallbox or self.identity.max_evse_current is None:
            return config
        return replace(config, current_cap=self.identity.max_evse_current)

    async def stable_snapshot(self, *, patient: bool = False) -> dict[str, Any]:
        """Publish a goal only after its enclosing command has completed.

        A ``patient`` reader waits two seconds for a running command and
        then gets the last published state instead, as long as that is recent:
        a long command must not make a poll time out, and a hanging one must
        not hide behind an old state.
        """
        if patient and self.command_lock.locked():
            try:
                await asyncio.wait_for(self.command_lock.acquire(), _PATIENCE_SECONDS)
            except TimeoutError:
                published = self.published
                if published is None or time.monotonic() - published[0] > _PUBLISHED_SECONDS:
                    raise
                return published[1]
            try:
                return self.publish()
            finally:
                self.command_lock.release()
        async with self.command_lock:
            return self.publish()

    def publish(self) -> dict[str, Any]:
        result = self.snapshot()
        self.published = (time.monotonic(), result)
        return result

    @property
    def choice_revision(self) -> str:
        """Fence app edits against explicit selections, not changing telemetry."""
        state = self.client.write_state
        raw = [self.soc_choice.revision, self.soc_choice.maximum_required, state.energy_goal_generation]
        return hashlib.sha256(json.dumps(raw).encode()).hexdigest()

    async def command(self, payload: dict[str, Any], *, _soc_outcome: Callable[[str, str, BaseException | None], None] | None = None) -> dict[str, Any]:
        payload = dict(payload)
        origin = payload.pop("origin", "unknown")
        if origin not in SOURCES:
            raise ValueError("invalid command origin")
        token = command_source.set(origin)
        try:
            return await self._command_with_origin(payload, _soc_outcome=_soc_outcome)
        finally:
            command_source.reset(token)

    async def _command_with_origin(
        self, payload: dict[str, Any], *,
        _soc_outcome: Callable[[str, str, BaseException | None], None] | None = None,
    ) -> dict[str, Any]:
        name = payload.get("command")
        if not isinstance(name, str) or (spec := COMMANDS.get(name)) is None:
            raise ValueError("unknown command")
        if spec.refuses(payload):
            raise ValueError("unknown command field")
        if "expected_serial_number" not in payload:
            raise ValueError("expected_serial_number is required")
        if spec.direct is not None:
            return await spec.direct(self, payload)
        expected_serial = payload["expected_serial_number"]
        if expected_serial is not None and (
            not isinstance(expected_serial, str) or not 1 <= len(expected_serial) <= 256
        ):
            raise ValueError("expected_serial_number must be a non-empty string or null")
        run = spec.run
        if run is None:
            raise ValueError("unknown command")
        async with self.command_lock:
            control = self.control
            written = self.diagnostics.writes_sent
            # A request can age while another command owns the lock. Only admit
            # it against the same current state that the API reports as usable.
            wallbox = needs_wallbox(name, payload)
            if control is None or self.stopping or (wallbox and not self.is_available()):
                raise DeviceError("service_unavailable")
            # The service can restart against another wallbox between HA polls,
            # or while this command waits. Bind the write before any bus access.
            # Explicit null matches only a device without a readable serial.
            if expected_serial != self.identity.serial_number:
                raise DeviceError("device_identity_changed")
            if "expected_choice_revision" in payload and payload["expected_choice_revision"] != self.choice_revision:
                raise DeviceError("soc_goal_source_changed")
            if "expected_soc_revision" in payload and payload["expected_soc_revision"] != self.soc_choice.revision:
                raise DeviceError("soc_goal_source_changed")
            if spec.reset:
                await run(self, control, payload)
                return self.snapshot()
            if self._intent_rejected:
                raise DeviceValidationError("read_only_mode")
            initial_revision = self.soc_choice.revision
            try:
                await run(self, control, payload)
            finally:
                failure = command_failed = sys.exception()
                try:
                    await self.persist()
                except BaseException as err:
                    if not isinstance(failure, (StorageCommitUncertainError, StorageStateUnknownError)):
                        failure = err
                    if isinstance(err, OSError) and command_failed is None:
                        # The wallbox has the command; only the file has not.
                        # That is not a service nobody could reach.
                        raise DeviceError("intent_not_saved") from err
                    raise
                finally:
                    if _soc_outcome is not None:
                        _soc_outcome(initial_revision, self.soc_choice.revision, failure)
            # Something was written during this command and a poll followed
            # the last write: the state to answer with is already read.
            read_back = written < self.diagnostics.writes_sent == self._read_back
            if wallbox and not read_back:
                await self.refresh()
            return self.publish()

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        # An allowlist of known reads, never a raw Modbus write tunnel.
        if set(payload) == {"register"}:
            key = payload["register"]
            if key != ERROR_CODE.key:
                raise ValueError("unsupported live register read")
            return {
                "value": await self.client.async_read_register(REGISTERS_BY_KEY[key])
            }
        if set(payload) == {"block"}:
            block = next(
                (
                    block
                    for block in REGISTER_BLOCKS
                    if block.name == payload["block"]
                    and "serial_number" not in block.keys
                ),
                None,
            )
            if block is None:
                raise ValueError("unsupported diagnostic block")
            return {
                "words": await self.client.async_read_words(
                    address=block.address, count=block.count
                )
            }
        raise ValueError("unsupported read")

    async def stop(self) -> None:
        try:
            await self.planner.shutdown()
        finally:
            await self._stop()

    async def _stop(self) -> None:
        self.stopping = True
        try:
            await self.soc_transactions.close()
            try:
                if self._task is not None:
                    self._task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._task
            finally:
                heartbeat_running = self.heartbeat.running
                await self.heartbeat.async_stop()
                if heartbeat_running:
                    _LOGGER.info("Heartbeat: stopped")
                if self.control is not None:
                    if self.goal is not None:
                        await self.goal.async_stop()
                    if self.soc_goal is not None:
                        await self.soc_goal.async_stop()
                    await self.control.async_shutdown()
                await self._save_charge_meter(force=True)
                # A disk that refuses must not turn a requested stop into a crash.
                await self.save_intent()
        finally:
            await self.client.async_close()
            _LOGGER.info("Connection: %s connection closed", self._connection_name)
