"""Serialize durable charging orders and their existing bounded actuator."""

from __future__ import annotations

import asyncio
import hmac
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .charge_job import MAX_BOOST_MINUTES, ChargeJob
from .charge_job_store import ChargeJobStore
from .client_errors import AmtronBusError
from .errors import DeviceError
from .guest_charge import guest_request
from .persistence import finish_goal_save


@dataclass(frozen=True, slots=True)
class JobInputs:
    """A passive snapshot. ``scope`` is a per-connection opaque digest, not a VIN."""

    now: datetime
    available: bool
    present: bool | None
    scope: str | None
    total_kwh: float | None
    meter_fresh: bool
    permitted: bool
    independent_stop: bool
    current_a: float | None
    current_cap: float
    pending: bool = False
    vehicle_target_active: bool = False


class JobRequestError(ValueError):
    """A bounded public error code, never raw storage or transport text."""


class ChargeJobController:
    """Persist permissions before starting; storage failure never prevents a stop.

    The actuator must use the existing control lock, write guards and command
    queue. It receives no release, unlock or arbitrary-register capability.
    Observations are synchronous so new stop evidence fences an in-flight write.
    """

    def __init__(
        self, store: ChargeJobStore, inputs: Callable[[], JobInputs],
        apply: Callable[[float | None, str, datetime], Awaitable[None]],
    ) -> None:
        self.store = store
        self.inputs = inputs
        self.apply = apply
        self.job: ChargeJob | None = None
        self.ready = False
        self.storage_error = False
        self.action_error: str | None = None
        self.revision = secrets.token_hex(16)
        self._durable: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._closing = False

    @property
    def holding(self) -> bool:
        return self.job is not None and not self.job.scope_ended

    async def initialize(self) -> None:
        async with self._lock:
            try:
                self.job = await asyncio.to_thread(self.store.load)
            except (OSError, ValueError):
                self.storage_error = True
            self.ready = True
            # A restored job is interrupted. Do not claim the old disk state is
            # already the newly latched stop; write that stop before any action.
            self._durable = None
            self._changed()

    def _changed(self) -> None:
        self.revision = secrets.token_hex(16)

    def _vehicle_conflict(self, sample: JobInputs) -> bool:
        return self.job is not None and self.job.kind == "guest" and sample.vehicle_target_active

    def observe(self) -> None:
        job = self.job
        if not self.ready or job is None:
            return
        sample = self.inputs()
        before = job.snapshot()
        job.observe(
            now=sample.now, available=sample.available, present=sample.present,
            matches=sample.scope == job.binding, total_kwh=sample.total_kwh,
            meter_fresh=sample.meter_fresh,
            independent_stop=sample.independent_stop or not sample.permitted or self._vehicle_conflict(sample),
        )
        if before != job.snapshot():
            self._changed()

    def stop_requested(self) -> bool:
        """Recheck time/freshness even between the scheduler's ordinary ticks."""
        if not self.ready or self.storage_error or self._closing:
            return True
        job = self.job
        if job is None or job.scope_ended:
            return False
        sample = self.inputs()
        return (
            job.stop_requested or self._durable != job.snapshot()
            or not sample.available or sample.present is not True
            or sample.scope != job.binding or not sample.meter_fresh
            or sample.independent_stop or not sample.permitted or self._vehicle_conflict(sample)
            or not job.allows_at(sample.now)
        )

    def current_policy(self, owner: str | None, enabling: bool) -> bool | None:
        """None keeps legacy control unchanged; True names our exact owner."""
        if not self.ready or self.storage_error or self._closing:
            return False
        if not self.holding:
            return None
        job = self.job
        if job is None or owner != f"service-job:{job.identifier}":
            return False if enabling else None
        return not self.stop_requested() if enabling else True

    def _can_boost(self, sample: JobInputs) -> bool:
        job = self.job
        return (
            self.ready and not self._closing and not self.storage_error
            and job is not None and job.kind != "guest" and not job.terminal and not job.scope_ended
            and sample.now < job.start_at and sample.available and sample.present is True
            and sample.scope == job.binding and sample.meter_fresh and sample.permitted
            and not sample.independent_stop and not sample.pending
        )

    def _boost_context(self, revision: object, scope: object) -> tuple[ChargeJob, JobInputs]:
        self._check_revision(revision)
        sample, job = self.inputs(), self.job
        if job is None or scope != sample.scope or not self._can_boost(sample):
            raise JobRequestError("job_boost_unavailable")
        return job, sample

    def preview_boost(self, minutes: object, *, revision: object, scope: object) -> dict[str, Any]:
        job, sample = self._boost_context(revision, scope)
        if type(minutes) is not int or not 1 <= minutes <= MAX_BOOST_MINUTES:
            raise JobRequestError("invalid_boost_duration")
        until = min(job.start_at, sample.now + timedelta(minutes=minutes))
        return {
            "schema": 1, "revision": self.revision, "scope": sample.scope,
            "until": until.isoformat(), "current_a": job.current_a,
            "energy_kwh": job.energy.target_kwh,
        }

    async def boost(self, until: object, *, revision: object, scope: object) -> dict[str, Any]:
        async with self._lock:
            job, sample = self._boost_context(revision, scope)
            # Absolute preview expiry: retrying cannot slide a relative timer.
            job.set_boost(until, now=sample.now)
            self._changed()
            await self._tick_locked()
            if self.storage_error:
                raise JobRequestError("job_storage_unavailable")
            return self.snapshot()

    async def cancel_boost(self, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check_revision(revision)
            if self.job is None:
                raise JobRequestError("job_boost_unavailable")
            if self.job.boost_until is not None:
                self.job.cancel_boost()
                self._changed()
            await self._tick_locked()
            return self.snapshot()

    def _check_revision(self, revision: object) -> None:
        if not isinstance(revision, str) or not hmac.compare_digest(revision, self.revision):
            raise JobRequestError("job_changed")

    @staticmethod
    def _request(job: ChargeJob) -> dict[str, Any]:
        return {
            "id": job.identifier, "start_at": job.start_at.isoformat(),
            "finish_at": job.finish_at.isoformat(), "current_a": job.current_a,
            "energy_kwh": job.energy.target_kwh,
            **({"kind": job.kind} if job.kind != "scheduled" else {}),
        }

    def _candidate(self, request: object, revision: object, scope: object) -> ChargeJob:
        self._check_revision(revision)
        sample = self.inputs()
        if not self.ready or self._closing or self.storage_error:
            raise JobRequestError("job_storage_unavailable")
        if self.holding:
            raise JobRequestError("job_exists")
        if (
            not sample.available or sample.present is not True
            or sample.scope is None or scope != sample.scope
            or not sample.meter_fresh or not sample.permitted
            or sample.independent_stop or sample.pending
        ):
            raise JobRequestError("job_not_permitted")
        candidate = ChargeJob.create(request, binding=sample.scope, total_kwh=sample.total_kwh, now=sample.now)
        if candidate.kind == "guest" and sample.vehicle_target_active:
            raise JobRequestError("guest_vehicle_target_active")
        if candidate.current_a > sample.current_cap:
            raise JobRequestError("job_current_exceeds_cap")
        return candidate

    def preview(self, request: object, *, revision: object, scope: object) -> dict[str, Any]:
        candidate = self._candidate(request, revision, scope)
        return {"schema": 1, "revision": self.revision, "scope": candidate.binding, "request": self._request(candidate)}

    def preview_guest(self, request: object, *, revision: object, scope: object) -> dict[str, Any]:
        """Produce one absolute proposal; confirmation reuses normal creation."""
        absolute = guest_request(request, now=self.inputs().now)
        return self.preview(absolute, revision=revision, scope=scope)

    async def create(self, request: object, *, revision: object, scope: object) -> dict[str, Any]:
        async with self._lock:
            if self.job is not None and isinstance(request, dict) and request == self._request(self.job):
                # Only return an old outcome. Never establish a second baseline.
                return self.snapshot()
            self.job = self._candidate(request, revision, scope)
            self._durable = None
            self._changed()
            await self._tick_locked()
            if self.storage_error:
                raise JobRequestError("job_storage_unavailable")
            return self.snapshot()

    async def stop(self, *, revision: object = None) -> dict[str, Any]:
        async with self._lock:
            if revision is not None:
                self._check_revision(revision)
            if self.job is not None:
                self.job.stop()
                self._changed()
            await self._tick_locked()
            return self.snapshot()

    async def clear(self, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check_revision(revision)
            sample = self.inputs()
            paused = sample.current_a is not None and 0 < sample.current_a < 6
            if not self.ready or not sample.available or sample.pending or (
                sample.present is not False and not paused
            ):
                raise JobRequestError("job_clear_requires_pause")
            if self.job is not None and not self.job.terminal:
                raise JobRequestError("job_stop_first")
            # Keep the in-memory stop until an explicitly confirmed clear has
            # become durable. Clearing never sends resume or release commands.
            try:
                await finish_goal_save(asyncio.to_thread(self.store.save, None))
            except (OSError, ValueError) as err:
                self.storage_error = True
                raise JobRequestError("job_storage_unavailable") from err
            self.job = None
            self._durable = None
            self.storage_error = False
            self.action_error = None
            self._changed()
            return self.snapshot()

    async def _save(self) -> None:
        job = self.job
        if job is None or job.snapshot() == self._durable:
            return
        raw = job.snapshot()
        frozen = ChargeJob.restore(raw, restarted=False)
        try:
            await finish_goal_save(asyncio.to_thread(self.store.save, frozen))
        except (OSError, ValueError):
            self.storage_error = True
            job.interrupt("storage_error")
            self._changed()
        else:
            self._durable = raw
            # A previous failed save is terminal for the job, even after retry.
            self.storage_error = False

    async def tick(self) -> None:
        async with self._lock:
            await self._tick_locked()

    async def _tick_locked(self) -> None:
        if not self.ready:
            return
        self.observe()
        await self._save()
        sample = self.inputs()
        if not sample.available or sample.present is not True:
            return
        job = self.job
        if not self.holding and not self.storage_error:
            return
        stop = self.stop_requested()
        current = sample.current_a
        target = None if stop or job is None else min(job.current_a, sample.current_cap)
        # Pending actions are owned by the existing controller. Do not replace
        # their queue on every tick. A newly required stop must still cancel a
        # queued start and is therefore deliberately not coalesced here.
        if target is None:
            if current is not None and 0 < current < 6 and not sample.pending:
                self.action_error = None
                return
        elif current == target:
            self.action_error = None
            return
        elif sample.pending:
            return
        try:
            await self.apply(target, f"service-job:{job.identifier}" if job else "service-job:storage", sample.now if job is None else job.finish_at)
        except (AmtronBusError, DeviceError, OSError, ValueError):
            self.action_error = "command_unconfirmed"
        else:
            self.action_error = None

    async def shutdown(self) -> None:
        async with self._lock:
            self._closing = True
            if self.job is not None:
                self.job.interrupt("service_restarted")
            await self._tick_locked()

    def snapshot(self) -> dict[str, Any]:
        sample = self.inputs()
        job = self.job
        summary = None
        if job is not None:
            baseline = job.energy.baseline_kwh
            progress = (
                max(0.0, sample.total_kwh - baseline)
                if sample.meter_fresh and sample.total_kwh is not None and baseline is not None
                and sample.scope == job.binding else None
            )
            summary = {
                **self._request(job), "state": job.state, "reason": job.reason,
                "scope_ended": job.scope_ended, "delivered_kwh": progress,
                "boost_until": job.boost_until.isoformat() if job.boost_until is not None else None,
            }
        return {
            "schema": 1, "revision": self.revision, "scope": sample.scope,
            "ready": self.ready, "storage_error": self.storage_error,
            "action_error": self.action_error, "job": summary,
            "available": sample.available, "present": sample.present,
            "permitted": sample.permitted, "meter_fresh": sample.meter_fresh,
            "max_current_a": sample.current_cap, "now": sample.now.isoformat(),
            "boost_available": self._can_boost(sample),
            "vehicle_target_active": sample.vehicle_target_active,
        }
