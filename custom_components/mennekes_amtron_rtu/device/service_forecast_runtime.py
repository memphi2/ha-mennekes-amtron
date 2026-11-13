"""Passive measured forecasts sharing the existing service and job responses."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .charge_job import ChargeJob
from .charge_rate import ChargeRateLearner, RateHistory, charging_forecast, context
from .charge_rate_store import RateStore
from .client import MennekesModbusClient
from .control import AmtronControl
from .enums import EvseState
from .persistence import finish_goal_save
from .service_config import ServerConfig
from .service_weekly_runtime import WeeklyModbusService
from .weekly_jobs import WeeklyChargeJobs

_LOGGER = logging.getLogger(__name__)
_SAVE_INTERVAL = 600.0


class ForecastJobs(WeeklyChargeJobs):
    """Attach advisory presentation without changing the existing command owner."""

    forecast: Callable[[ChargeJob | None], dict[str, Any]] | None = None

    def snapshot(self) -> dict[str, Any]:
        ordinary = super().snapshot()
        return {**ordinary, **({"forecast": self.forecast(self.job)} if self.forecast is not None else {})}


class ForecastModbusService(WeeklyModbusService):
    """Optional telemetry cannot grant commands or become a second bus owner."""

    jobs: ForecastJobs | None = None
    job_controller_type = ForecastJobs

    def __init__(self, config: ServerConfig, state_path: Path, *, client: MennekesModbusClient | None = None) -> None:
        self.rates = ChargeRateLearner()
        self.forecast_clock: Callable[[], float] = time.monotonic
        self._rate_store: RateStore | None = None
        self._rate_save: asyncio.Task[None] | None = None
        self._rate_saved_generation = 0
        self._rate_save_at = 0.0
        self._rate_storage_error = False
        self._forecast_closing = False
        super().__init__(config, state_path, client=client)

    def _configure_control(self, control: AmtronControl) -> None:
        super()._configure_control(control)
        if self.jobs is None:
            raise RuntimeError("job controller was not configured")
        self.jobs.forecast = self._job_forecast

    async def _initialize(self) -> None:
        self.rates.invalidate()
        await super()._initialize()
        identity = self.identity.serial_number or f"{self.config.connection.port}:{self.config.connection.device_id}"
        self._rate_store = RateStore(self.state_path.with_name("charge-rates.json"), identity)
        self.rates.history = RateHistory()
        try:
            self.rates.history = await asyncio.to_thread(self._rate_store.load)
        except (OSError, ValueError):
            self._rate_storage_error = True
            _LOGGER.warning("Optional charging-rate history unavailable; learning new samples")
        else:
            self._rate_storage_error = False
        self._rate_saved_generation = self.rates.history.generation
        self._rate_save_at = self.forecast_clock() + _SAVE_INTERVAL

    def _rate_profile(self, job: ChargeJob | None, scope: str | None) -> str | None:
        if job is None or job.kind == "guest" or job.scope_ended or job.binding != scope:
            return None
        consent = self.weekly.consent if self.weekly is not None else None
        source = self.control.soc_goal.source if self.control is not None else None
        reference = (
            f"weekly:{consent.profile}" if consent is not None and consent.job_id == job.identifier
            else f"source:{source}" if isinstance(source, str) and source else None
        )
        return hashlib.sha256(reference.encode()).hexdigest() if reference is not None else None

    def _rate_phases(self) -> int:
        phases = self.data.fresh("detected_ev_phases")
        return int(phases) if isinstance(phases, (int, float)) and not isinstance(phases, bool) and phases in (1, 2, 3) else 0

    def _job_forecast(self, job: ChargeJob | None) -> dict[str, Any]:
        sample = self._job_inputs()
        result = charging_forecast(
            self.rates, job, sample, profile=self._rate_profile(job, sample.scope),
            phases=self._rate_phases(), monotonic=self.forecast_clock(),
            charging=self.data.fresh("evse_state") == EvseState.CHARGING,
            meter_at=self.data.energy_observed_at,
        )
        return {**result, "history_storage": "unavailable" if self._rate_storage_error else "ok"}

    async def refresh(self) -> None:
        try:
            await super().refresh()
        except BaseException:
            self.rates.invalidate("unavailable")
            raise
        sample = self._job_inputs()
        job = self.jobs.job if self.jobs is not None else None
        key = context(sample.scope, self._rate_profile(job, sample.scope), sample.current_a, self._rate_phases())
        charging = sample.available and sample.present is True and self.data.fresh("evse_state") == EvseState.CHARGING
        self.rates.check_context(key, charging=charging)
        if self.data.energy_read:
            self.rates.observe(
                key, energy=sample.total_kwh if sample.meter_fresh else None,
                at=self.data.energy_observed_at, monotonic=self.forecast_clock(),
                now=sample.now.timestamp(), charging=charging,
            )
        self._start_rate_save()

    def _start_rate_save(self, *, force: bool = False) -> None:
        store = self._rate_store
        if (
            store is None or (self._forecast_closing and not force)
            or (self._rate_save is not None and not self._rate_save.done())
            or self.rates.history.generation == self._rate_saved_generation
            or (not force and self.forecast_clock() < self._rate_save_at)
        ):
            return
        generation, frozen = self.rates.history.generation, self.rates.history.snapshot()
        self._rate_save_at = self.forecast_clock() + _SAVE_INTERVAL

        async def save() -> None:
            try:
                await asyncio.to_thread(store.save, frozen)
            except (OSError, ValueError):
                if not self._rate_storage_error:
                    _LOGGER.warning("Optional charging-rate history could not be saved")
                self._rate_storage_error = True
            else:
                self._rate_saved_generation = generation
                self._rate_storage_error = False

        self._rate_save = asyncio.create_task(save(), name="amtron_rate_history")

    async def stop(self) -> None:
        self._forecast_closing = True
        try:
            await super().stop()
        finally:
            # Charging/bus owners stop first. Join the one optional save worker,
            # then flush any newer sample; never orphan an atomic file write.
            if self._rate_save is not None:
                await finish_goal_save(self._rate_save)
            self._start_rate_save(force=True)
            if self._rate_save is not None:
                await finish_goal_save(self._rate_save)
