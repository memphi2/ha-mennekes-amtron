"""Run bounded charging orders beside the existing single Modbus owner."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .charge_job import UNKNOWN_GRACE, SessionMark
from .charge_job_controller import ChargeJobController, JobInputs
from .charge_job_store import ChargeJobStore
from .control import AmtronControl, ServiceChargePolicy
from .errors import DeviceError, validation_error
from .service_console_runtime import ConsoleModbusService
from .vehicle import snapshot_vehicle_connected

_LOGGER = logging.getLogger(__name__)
# A meter stamp further from the wall clock than this is no reading of now.
_STAMP_TOLERANCE_SECONDS = 24 * 3600.0
# Goal metadata may still reduce a limit. Competing planners and enabling
# manual writes must explicitly relinquish the local job before taking over.
_COMPETING = frozenset({"set_current", "resume", "timed_current", "solar", "phases", "recover", "restart"})
# The wallbox state an order's stop is judged on.
_STATUS_KEYS = ("error_code", "lock_evse", "charging_release")


class JobModbusService(ConsoleModbusService):
    """Keep jobs local across HA disconnects; continuity needs wallbox proof."""

    jobs: ChargeJobController | None = None
    _job_scope: str | None = None
    # Since when the plug state is unreadable; a short gap keeps the scope.
    _job_unknown_since: float | None = None
    # This connection's session clock and energy with their read time, and
    # the lifetime session counter; cleared with the scope.
    _job_session: dict[str, Any] | None = None
    # Whether Plug & Charge held the release closed during the latest poll:
    # a grant opens it before a poll can show that.
    _gate_read = False

    def _configure_control(self, control: AmtronControl) -> None:
        identity = self.identity.serial_number or f"{self.config.connection.port}:{self.config.connection.device_id}"
        self.jobs = ChargeJobController(
            ChargeJobStore(self.state_path.with_name("charge-job.json"), identity),
            self._job_inputs, self._apply_job_current,
        )
        jobs = self.jobs
        control.set_service_policy(ServiceChargePolicy(
            jobs.current_policy, jobs.stop_requested, lambda: jobs.stop_requested(gate=False),
        ))

    async def _initialize(self) -> None:
        self._job_scope = None
        self._job_unknown_since = None
        self._job_session = None
        self._gate_read = False
        await super()._initialize()
        if self.jobs is None:
            raise RuntimeError("job controller was not configured")
        await self.jobs.initialize()
        if self.jobs.storage_error:
            _LOGGER.error("Saved charge job unavailable; charging starts fenced until explicit local reset")

    def _job_inputs(self) -> JobInputs:
        control = self.control
        available = self._is_available() and not self._stopping
        present = snapshot_vehicle_connected(
            self.data.fresh, self.identity.layout_version,
        ) if available else None
        # Policy checks run before every write; read the sample, not a snapshot.
        sample = self._energy_sample
        now = datetime.now(UTC)
        total = sample.get("value") if isinstance(sample, dict) else None
        stamp = sample.get("observed_at") if isinstance(sample, dict) else None
        # Judged on the monotonic clock: a wall-clock step must not pause an
        # order for a meter reading that is not old at all. The stamp itself
        # only has to be a plausible time.
        age = time.monotonic() - self._energy_sample_at if self._energy_sample_at is not None else None
        fresh = (
            available and isinstance(total, (int, float)) and not isinstance(total, bool)
            and math.isfinite(total) and total >= 0
            and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and math.isfinite(stamp) and abs(now.timestamp() - stamp) <= _STAMP_TOLERANCE_SECONDS
            and age is not None and 0 <= age <= max(90, self.config.scan_interval * 3)
        )
        scope = session = None
        independent_stop = False
        if control is not None:
            if self._job_scope is not None:
                selection = f"{control.soc_goal.source}|{control.plug_charge['policy']}"
                scope = hashlib.sha256(f"{self._job_scope}|{selection}".encode()).hexdigest()
                reading = self._job_session
                if available and reading is not None and reading.keys() >= {"at", "duration_s", "energy_kwh", "count"}:
                    session = SessionMark(
                        reading["at"], reading["duration_s"], reading["energy_kwh"], reading["count"],
                        hashlib.sha256(selection.encode()).hexdigest(),
                    )
            independent_stop = control.goal_stop_requested
        current = self.data.fresh("charging_current_ems")
        valid_current = (
            available and isinstance(current, (int, float)) and not isinstance(current, bool)
            and math.isfinite(current) and current >= 0
            and self.data.current_write_generation == self.client.write_state.current_write_generation
        )
        sound = (
            control is not None and not control.read_only
            and self.data.fresh("error_code") == 0 and self.data.fresh("lock_evse") == 0
            and control.charging_release_requested is not False
        )
        gate = self.client.write_state.plug_charge.blocked
        permitted = sound and not gate and self.data.fresh("charging_release") == 1
        # The gate closes the release itself; nobody switched it off. Until a
        # poll ran with the gate open, a closed release is still the gate's.
        release_waiting = sound and (gate or self._gate_read) and not permitted
        # One lost reply holds these values: no stop can be told from a gap.
        status_known = not any(key in self.data.held for key in _STATUS_KEYS)
        current_value = float(current) if valid_current and isinstance(current, (int, float)) else None
        total_value = float(total) if fresh and isinstance(total, (int, float)) else None
        return JobInputs(
            now, available, present, scope, total_value, fresh,
            permitted, independent_stop, current_value,
            control.max_charging_current if control is not None else 6,
            control is not None and control.pending_action is not None,
            vehicle_target_active=control is not None and control.soc_goal.target_percent > 0,
            session=session, release_waiting=release_waiting, status_known=status_known,
            read_only=control is not None and control.read_only,
        )

    def _note_session(self) -> None:
        """Keep the newest wallbox reading of this connection's plug-in."""
        data, reading = self.data, dict(self._job_session or {})
        duration, energy = data.fresh("session_duration"), data.fresh("session_energy")
        if (
            data.session_read and type(duration) is int and duration >= 0
            and isinstance(energy, float) and math.isfinite(energy) and energy >= 0
        ):
            reading.update(at=datetime.now(UTC), duration_s=duration, energy_kwh=energy)
        count = data.fresh("sessions_total")
        if data.energy_read and type(count) is int and count >= 0:
            reading["count"] = count
        self._job_session = reading

    async def refresh(self) -> None:
        polled = False
        gate = self.client.write_state.plug_charge.blocked
        try:
            await super().refresh()
            polled = True
        finally:
            if polled:
                self._gate_read = gate or self.client.write_state.plug_charge.blocked
            present = snapshot_vehicle_connected(
                self.data.fresh, self.identity.layout_version,
            ) if self._is_available() else None
            if present is True:
                self._job_unknown_since = None
                if self._job_scope is None:
                    self._job_scope = secrets.token_hex(32)
            elif present is False:
                self._job_scope = self._job_unknown_since = None
            elif self._job_unknown_since is None:
                self._job_unknown_since = time.monotonic()
            elif time.monotonic() - self._job_unknown_since >= UNKNOWN_GRACE.total_seconds():
                # Only a lasting gap could hide an unplug; a new car needs
                # a new order or the wallbox's proof of the same plug-in.
                self._job_scope = None
            if self._job_scope is None:
                self._job_session = None
            elif polled:
                self._note_session()
            if self.jobs is not None:
                self.jobs.observe()

    async def _apply_job_current(self, value: float | None, owner: str, deadline: datetime) -> None:
        async with self._command_lock:
            control = self.control
            if control is None or self._stopping or not self._is_available():
                raise DeviceError("service_unavailable")
            try:
                if value is None:
                    await control.async_cancel_timed_current(owner)
                    await control.async_pause_for_energy_goal()
                else:
                    await control.async_set_timed_current(value, deadline=deadline, owner=owner)
            finally:
                await self._persist()

    async def command(
        self, payload: dict[str, Any], *,
        _soc_outcome: Callable[[str, str, BaseException | None], None] | None = None,
    ) -> dict[str, Any]:
        jobs = self.jobs
        if jobs is not None and jobs.holding and "expected_serial_number" in payload and payload["expected_serial_number"] == self.identity.serial_number:
            command, value = payload.get("command"), payload.get("value")
            if (isinstance(command, str) and command in _COMPETING) or (command == "release" and value is True) or (command == "lock" and value is False):
                raise validation_error("service_job_active")
            if command in ("pause", "cancel_pending") or (
                command == "cancel_timed_current" and jobs.job is not None
                and payload.get("owner") == f"service-job:{jobs.job.identifier}"
            ) or (command == "release" and value is False) or (command == "lock" and value is True):
                # HA detaches its own timed owner during a restart. Cancelling
                # that unrelated owner must not cancel a service-owned order.
                if payload.keys() - {"command", "value", "owner", "expected_serial_number"}:
                    raise ValueError("unsupported stop command field")
                await jobs.stop()
        return await super().command(payload, _soc_outcome=_soc_outcome)

    async def _run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._run_bus(), name="amtron_bus_owner")
            group.create_task(self._jobs_loop(), name="amtron_charge_jobs")

    async def _run_bus(self) -> None:
        await super()._run()
        raise RuntimeError("Modbus bus owner stopped unexpectedly")

    async def _jobs_loop(self) -> None:
        while True:
            if self.jobs is not None:
                try:
                    await self.jobs.tick()
                except Exception:
                    # The bus owner shares this task group. An order failing
                    # in an unforeseen way must not stop the whole service;
                    # its writes stay fenced by the policy meanwhile.
                    _LOGGER.exception("Charge job tick failed")
                    self.jobs.action_error = "command_unconfirmed"
            await asyncio.sleep(5 if self.jobs is not None and (self.jobs.storage_error or self.jobs.action_error) else 1)

    async def stop(self) -> None:
        try:
            if self.jobs is not None:
                await self.jobs.shutdown()
        finally:
            await super().stop()
