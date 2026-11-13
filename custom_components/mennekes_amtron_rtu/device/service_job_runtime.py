"""Run bounded charging orders beside the existing single Modbus owner."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .charge_job_controller import ChargeJobController, JobInputs
from .charge_job_store import ChargeJobStore
from .control import AmtronControl, ServiceChargePolicy
from .errors import DeviceError
from .registers import CP_STATE
from .service_console_runtime import ConsoleModbusService
from .vehicle import vehicle_connected

_LOGGER = logging.getLogger(__name__)
# Goal metadata may still reduce a limit. Competing planners and enabling
# manual writes must explicitly relinquish the local job before taking over.
_COMPETING = frozenset({"set_current", "resume", "timed_current", "solar", "phases", "recover", "restart"})


class JobModbusService(ConsoleModbusService):
    """Keep jobs local across HA disconnects; never assume continuity on boot."""

    jobs: ChargeJobController | None = None
    _job_scope: str | None = None

    def _configure_control(self, control: AmtronControl) -> None:
        identity = self.identity.serial_number or f"{self.config.connection.port}:{self.config.connection.device_id}"
        self.jobs = ChargeJobController(
            ChargeJobStore(self.state_path.with_name("charge-job.json"), identity),
            self._job_inputs, self._apply_job_current,
        )
        control.set_service_policy(ServiceChargePolicy(self.jobs.current_policy, self.jobs.stop_requested))

    async def _initialize(self) -> None:
        self._job_scope = None
        await super()._initialize()
        if self.jobs is None:
            raise RuntimeError("job controller was not configured")
        await self.jobs.initialize()
        if self.jobs.storage_error:
            _LOGGER.error("Saved charge job unavailable; charging starts fenced until explicit local reset")

    def _job_inputs(self) -> JobInputs:
        control = self.control
        available = self._is_available() and not self._stopping
        present = vehicle_connected(
            self.data.fresh("evse_state"), self.data.fresh("cp_state"),
            cp_supported=self.identity.layout_version >= CP_STATE.min_layout,
        ) if available else None
        sample = self.snapshot().get("energy_sample")
        now = datetime.now(UTC)
        total = sample.get("value") if isinstance(sample, dict) else None
        stamp = sample.get("observed_at") if isinstance(sample, dict) else None
        fresh = (
            available and isinstance(total, (int, float)) and not isinstance(total, bool)
            and math.isfinite(total) and total >= 0
            and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and 0 <= now.timestamp() - stamp <= max(90, self.config.scan_interval * 3)
        )
        scope = None
        independent_stop = False
        if control is not None:
            if self._job_scope is not None:
                scope = hashlib.sha256(
                    f"{self._job_scope}|{control.soc_goal.source}|{control.plug_charge['policy']}".encode()
                ).hexdigest()
            independent_stop = (
                control.goal_controller.stop_requested if control.goal_controller is not None else control.energy_goal.stop_requested
            ) or (
                control.soc_goal_controller.stop_requested if control.soc_goal_controller is not None else control.soc_goal.stop_requested
            )
        current = self.data.fresh("charging_current_ems")
        valid_current = (
            available and isinstance(current, (int, float)) and not isinstance(current, bool)
            and math.isfinite(current) and current >= 0
            and self.data.current_write_generation == self.client.write_state.current_write_generation
        )
        permitted = (
            control is not None and not control.read_only
            and self.data.fresh("error_code") == 0 and self.data.fresh("lock_evse") == 0
            and self.data.fresh("charging_release") == 1
            and not self.client.write_state.plug_charge.blocked
            and control.charging_release_requested is not False
        )
        current_value = float(current) if valid_current and isinstance(current, (int, float)) else None
        total_value = float(total) if fresh and isinstance(total, (int, float)) else None
        return JobInputs(
            now, available, present, scope, total_value, fresh,
            permitted, independent_stop, current_value,
            control.max_charging_current if control is not None else 6,
            control is not None and control.pending_action is not None,
            vehicle_target_active=control is not None and control.soc_goal.target_percent > 0,
        )

    async def refresh(self) -> None:
        try:
            await super().refresh()
        finally:
            present = vehicle_connected(
                self.data.fresh("evse_state"), self.data.fresh("cp_state"),
                cp_supported=self.identity.layout_version >= CP_STATE.min_layout,
            ) if self._is_available() else None
            if present is not True:
                self._job_scope = None
            elif self._job_scope is None:
                self._job_scope = secrets.token_hex(32)
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
                raise DeviceError("energy_goal_stop_active")
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
                await self.jobs.tick()
            await asyncio.sleep(5 if self.jobs is not None and (self.jobs.storage_error or self.jobs.action_error) else 1)

    async def stop(self) -> None:
        try:
            if self.jobs is not None:
                await self.jobs.shutdown()
        finally:
            await super().stop()
