"""Session-confirmed recurrence beside the existing heartbeat and job owner."""

from __future__ import annotations

import asyncio
import logging

from .charge_job_store import ChargeJobStore
from .control import AmtronControl, ServiceChargePolicy
from .service_job_runtime import JobModbusService
from .weekly_controller import WeeklyController
from .weekly_jobs import WeeklyChargeJobs
from .weekly_store import WeeklyStore

_LOGGER = logging.getLogger(__name__)


class WeeklyModbusService(JobModbusService):
    """Recipes survive restarts; vehicle/session consent deliberately does not."""

    jobs: WeeklyChargeJobs | None = None
    weekly: WeeklyController | None = None
    job_controller_type: type[WeeklyChargeJobs] = WeeklyChargeJobs

    def _configure_control(self, control: AmtronControl) -> None:
        identity = self.identity.serial_number or f"{self.config.connection.port}:{self.config.connection.device_id}"
        self.jobs = self.job_controller_type(
            ChargeJobStore(self.state_path.with_name("charge-job.json"), identity),
            self._job_inputs, self._apply_job_current,
        )
        control.set_service_policy(ServiceChargePolicy(self.jobs.current_policy, self.jobs.stop_requested))
        self.weekly = WeeklyController(
            WeeklyStore(self.state_path.with_name("weekly-plans.json"), identity), self.jobs,
        )

    async def _initialize(self) -> None:
        await super()._initialize()
        if self.weekly is None:
            raise RuntimeError("weekly controller was not configured")
        await self.weekly.initialize()

    async def _jobs_loop(self) -> None:
        while True:
            if self.jobs is not None:
                try:
                    await self.jobs.tick()
                except Exception:
                    # As in the job service: an unforeseen failure must not
                    # stop the bus owner; writes stay fenced by the policy.
                    _LOGGER.exception("Charge job tick failed")
                    self.jobs.action_error = "command_unconfirmed"
            if self.weekly is not None:
                try:
                    await self.weekly.tick()
                except Exception:
                    _LOGGER.exception("Weekly plan tick failed")
            await asyncio.sleep(5 if self.jobs is not None and (self.jobs.storage_error or self.jobs.action_error) else 1)

    async def stop(self) -> None:
        try:
            if self.weekly is not None:
                await self.weekly.shutdown()
        finally:
            await super().stop()
