"""Serialize durable charging orders and their existing bounded actuator."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .charge_job import (
    CURRENT_TOLERANCE_A,
    ChargeJob,
    SessionMark,
    job_time,
)
from .charge_job_store import ChargeJobStore
from .client_errors import AmtronBusError
from .errors import DeviceError
from .guest_charge import guest_request
from .persistence import finish_goal_save


def _preview_expired(request: object, now: datetime) -> bool:
    """The request starts more than the accepted minute before ``now``."""
    if not isinstance(request, dict):
        return False
    try:
        start = job_time(request.get("start_at"))
    except ValueError:
        return False
    return start < now - timedelta(minutes=1)


def _decision(raw: dict[str, Any]) -> dict[str, Any]:
    """The saved part that matters; the session record refreshes in RAM."""
    return {key: value for key, value in raw.items() if key != "session"}


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
    # Writes are disabled in the app configuration.
    read_only: bool = False
    # Only the Plug & Charge release is missing, as after an app restart:
    # an order waits for Home Assistant's grant instead of ending.
    release_waiting: bool = False
    # False while the error, lock or release value is held from a lost reply.
    status_known: bool = True
    # The wallbox's record of the current plug-in, None until read in full.
    session: SessionMark | None = None


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
        # A stop that was asked for but has not finished yet; see stop().
        self._stop_unconfirmed = False
        # A finished order whose pause the wallbox confirmed hands control
        # back; the pause stays as ordinary charging intent.
        self._settled = False

    @property
    def holding(self) -> bool:
        return self.job is not None and not self.job.scope_ended and not self._settled

    async def initialize(self) -> None:
        async with self._lock:
            try:
                self.job = await asyncio.to_thread(self.store.load)
            except (OSError, ValueError, ArithmeticError, RecursionError):
                # A deeply nested file or an oversized number is as corrupt
                # as an invalid one.
                self.storage_error = True
            self.ready = True
            # A restored job is interrupted. Do not claim the old disk state is
            # already the newly latched stop; write that stop before any action.
            self._durable = None
            self._changed()

    def _changed(self) -> None:
        self.revision = secrets.token_hex(16)

    def _vehicle_conflict(self, sample: JobInputs) -> bool:
        return False

    _starting_guest = False

    @property
    def guest_active(self) -> bool:
        return self.job is not None and self.job.kind == "guest" and self.holding

    async def start_shared(self, request: dict[str, Any], grant: Callable[[], Awaitable[None]]) -> dict[str, Any]:
        """Save the session before granting release; never start without durable limits."""
        async with self._lock:
            sample = self.inputs()
            if self.holding or not self.ready or self.storage_error or self._closing:
                raise JobRequestError("job_not_permitted")
            if sample.read_only or not sample.available or sample.present is not True or sample.scope is None or not sample.status_known:
                raise JobRequestError("job_not_permitted")
            self.job = ChargeJob.create(request, binding=sample.scope, total_kwh=sample.total_kwh, now=sample.now)
            self.job.session = sample.session
            self._durable = None
            self._settled = False
            self._changed()
            await self._save()
            if self.storage_error:
                raise JobRequestError("job_storage_unavailable")
            self._starting_guest = True
            try:
                await grant()
            except BaseException:
                self.job.stop()
                self._changed()
                await self._save()
                raise
            finally:
                self._starting_guest = False
            await self._tick_locked()
            return self.snapshot()

    async def update_guest_deadline(self, deadline: datetime | None) -> None:
        async with self._lock:
            if not self.guest_active or self.job is None or self.job.terminal or self.job.finish_at == deadline:
                return
            if deadline is not None and deadline <= self.job.start_at:
                self.job.stop()
            else:
                self.job.finish_at = deadline
            self._changed()
            await self._tick_locked()

    def observe(self) -> None:
        job = self.job
        if not self.ready or job is None or self._starting_guest:
            return
        sample = self.inputs()
        before = _decision(job.snapshot())
        if (
            sample.session is not None and sample.scope == job.binding
            and not job.terminal and job.state != "suspended"
        ):
            job.session = sample.session
        # A closing service must not continue its own suspended order.
        closing = self._closing
        job.observe(
            # A held status is a short gap: it cannot prove a stop.
            now=sample.now, available=sample.available and sample.status_known, present=sample.present,
            matches=sample.scope == job.binding, total_kwh=sample.total_kwh,
            meter_fresh=sample.meter_fresh,
            independent_stop=(
                sample.independent_stop or (not sample.permitted and not sample.release_waiting)
                or self._vehicle_conflict(sample)
            ),
            scope=None if closing else sample.scope, session=None if closing else sample.session,
        )
        if before != _decision(job.snapshot()):
            self._changed()

    def stop_requested(self, *, gate: bool = True) -> bool:
        """Recheck time/freshness even between the scheduler's ordinary ticks.

        ``gate=False`` leaves out a release that only Plug & Charge keeps
        closed: the order waits for that grant, which may then open it.
        """
        if not self.ready or self.storage_error or self._closing or self._stop_unconfirmed:
            return True
        if self._starting_guest:
            return False
        job = self.job
        if job is None or job.scope_ended or self._settled:
            return False
        sample = self.inputs()
        permitted = sample.permitted or (not gate and sample.release_waiting)
        return (
            job.stop_requested or self._durable != _decision(job.snapshot())
            or not sample.available or sample.present is not True
            or sample.scope != job.binding or not sample.meter_fresh
            or sample.independent_stop or not permitted or self._vehicle_conflict(sample)
            or not job.allows_at(sample.now, total_kwh=sample.total_kwh)
        )

    def current_policy(self, owner: str | None, enabling: bool) -> bool | None:
        """None leaves direct charging control unchanged; True names our exact owner.

        An unready, failed or closing store fences only starts. A pause or a
        lower limit from another owner always runs.
        """
        if not self.ready or self.storage_error or self._closing:
            return False if enabling else None
        if enabling and self._stop_unconfirmed:
            # Fence an in-flight start before the stop obtains the owner lock.
            return False
        if not self.holding or self._starting_guest:
            return None
        job = self.job
        if job is None or owner != f"service-job:{job.identifier}":
            return False if enabling else None
        return not self.stop_requested() if enabling else True

    def _check_revision(self, revision: object) -> None:
        # Revisions are public opaque tokens, not secrets. Unicode must be a
        # normal conflict, never a compare_digest TypeError.
        if not isinstance(revision, str) or revision != self.revision:
            raise JobRequestError("job_changed")

    @staticmethod
    def _request(job: ChargeJob) -> dict[str, Any]:
        return {
            "id": job.identifier, "start_at": job.start_at.isoformat(),
            "finish_at": job.finish_at.isoformat() if job.finish_at else None, "current_a": job.current_a,
            "energy_kwh": job.energy.target_kwh,
            **({"kind": job.kind} if job.kind != "scheduled" else {}),
        }

    def _candidate(self, request: object, revision: object, scope: object) -> ChargeJob:
        self._check_revision(revision)
        sample = self.inputs()
        if not self.ready or self._closing or self.storage_error:
            raise JobRequestError("job_storage_unavailable")
        if self._stop_unconfirmed:
            raise JobRequestError("job_stop_unconfirmed")
        if self.holding:
            raise JobRequestError("job_exists")
        if (
            sample.read_only or not sample.available or sample.present is not True
            or sample.scope is None or scope != sample.scope
            or not sample.meter_fresh or not sample.permitted
            or sample.independent_stop or sample.pending
        ):
            raise JobRequestError("job_not_permitted")
        try:
            candidate = ChargeJob.create(request, binding=sample.scope, total_kwh=sample.total_kwh, now=sample.now)
        except ValueError as err:
            if _preview_expired(request, sample.now):
                # A preview fixes absolute times; an old one needs a new preview.
                raise JobRequestError("job_preview_expired") from err
            raise
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
            return await self._create_locked(request, revision=revision, scope=scope)

    async def _create_locked(self, request: object, *, revision: object, scope: object) -> dict[str, Any]:
        if self.job is not None and isinstance(request, dict) and request == self._request(self.job):
            return self.snapshot()
        self.job = self._candidate(request, revision, scope)
        self._durable = None
        self._settled = False
        self._changed()
        await self._tick_locked()
        if self.storage_error:
            raise JobRequestError("job_storage_unavailable")
        return self.snapshot()

    async def stop(self, *, revision: object = None) -> dict[str, Any]:
        if revision is not None:
            self._check_revision(revision)
        # The stop counts from now on, not from when it obtains the lock. A
        # request cut off while it waits must not withdraw it; an explicit
        # clear then removes this fence, but only after its pause checks.
        self._stop_unconfirmed = True
        async with self._lock:
            try:
                if self.job is not None:
                    self.job.stop()
                    self._changed()
                await self._tick_locked()
            finally:
                # The stopped job now holds the stop itself. Whatever the
                # tick did, this fence must not outlive it and block every
                # later start without a job to show for it.
                self._stop_unconfirmed = False
        return self.snapshot()

    async def clear(self, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check_revision(revision)
            sample = self.inputs()
            paused = sample.current_a is not None and 0 < sample.current_a < 6
            if not self.ready:
                raise JobRequestError("job_clear_requires_pause")
            if self._clear_needs_pause() and (not sample.available or sample.pending or (
                sample.present is not False and not paused
            )):
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
            self._settled = False
            self.storage_error = False
            self.action_error = None
            self._stop_unconfirmed = False
            self._changed()
            return self.snapshot()

    def _clear_needs_pause(self) -> bool:
        """An order that still holds the car, or a stop still in flight, needs its pause first."""
        return self._stop_unconfirmed or self.holding

    async def _save(self) -> None:
        job = self.job
        if job is None or self._settled or _decision(job.snapshot()) == self._durable:
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
            self._durable = _decision(raw)
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
        if not sample.available or sample.present is not True or not sample.status_known:
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
                await self._settle()
                return
        elif current is not None and abs(current - target) < CURRENT_TOLERANCE_A:
            self.action_error = None
            return
        elif sample.pending:
            return
        try:
            await self.apply(target, f"service-job:{job.identifier}" if job else "service-job:storage", job.finish_at if job is not None and job.finish_at is not None else sample.now + timedelta(hours=2))
        except (AmtronBusError, DeviceError, OSError, ValueError):
            self.action_error = "command_unconfirmed"
        else:
            self.action_error = None

    async def _settle(self) -> None:
        """Release a finished order once its pause is confirmed and saved.

        The pause is then ordinary charging intent, so HA may resume. Saving
        "no job" keeps a restart from restoring the order and pausing again.
        """
        job = self.job
        if (
            job is None or not job.terminal or job.scope_ended or self._settled
            or self.storage_error or self._durable != _decision(job.snapshot())
        ):
            return
        try:
            await finish_goal_save(asyncio.to_thread(self.store.save, None))
        except (OSError, ValueError):
            # Keep holding; the next tick tries again.
            return
        self._settled = True
        self._changed()

    async def shutdown(self) -> None:
        async with self._lock:
            self._closing = True
            if self.job is not None:
                # Save the latest session record with the suspension, so a
                # restart can prove the same plug-in and continue the order.
                self.observe()
                self.job.suspend("service_restarted")
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
                "scope_ended": job.scope_ended, "holding": self.holding,
                "delivered_kwh": progress,
            }
        return {
            "schema": 1, "revision": self.revision, "scope": sample.scope,
            "ready": self.ready, "storage_error": self.storage_error,
            "action_error": self.action_error, "job": summary,
            "available": sample.available, "present": sample.present,
            "permitted": sample.permitted, "meter_fresh": sample.meter_fresh,
            "max_current_a": sample.current_cap, "now": sample.now.isoformat(),
            "vehicle_target_active": sample.vehicle_target_active,
            "read_only": sample.read_only,
            "manual_stop_pending": self._stop_unconfirmed,
        }
