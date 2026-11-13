"""Session-confirmed weekly recipes feeding the single durable job scheduler."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .charge_job import job_time
from .departure_model import vehicle_key
from .errors import DeviceError
from .persistence import finish_goal_save
from .weekly_plan import WeeklyOccurrence, WeeklyPlan
from .weekly_store import WeeklyBook, WeeklyStore

if TYPE_CHECKING:
    from .weekly_jobs import WeeklyChargeJobs


class WeeklyError(ValueError):
    """Stable public refusal code, without raw storage or device error text."""


@dataclass(slots=True)
class SessionConsent:
    plan_id: str
    profile: str
    scope: str
    expires: datetime
    stop_epoch: int
    last_now: datetime
    job_id: str | None = None


class WeeklyController:
    """Persist at-most-once occurrence claims and the car a plan was activated for.

    One activation covers only the currently confirmed connection for at most
    seven days. Recipe edits, manual stops, restart, unplugging or a lasting
    connection gap end it. A completed occurrence hands control back to Home
    Assistant; the next one starts at its own window, never after a stopped or
    interrupted occurrence. A failed/uncertain claim is not silently replayed.

    A plan activated by hand while Home Assistant had detected the car at
    that plug-in remembers the car. Once Home Assistant detects the same car
    at a later plug-in, or again after a restart, the plan activates itself
    and waits for the next window. Stopping the plan or its order by hand
    forgets the car.
    """

    def __init__(
        self, store: WeeklyStore, jobs: WeeklyChargeJobs,
        confirmed: Callable[[], tuple[str, str] | None] | None = None,
    ) -> None:
        self.store = store
        self.jobs = jobs
        # The car Home Assistant confirmed and the plug-in it confirmed it for.
        self.confirmed = confirmed
        # The plug-in that already had its activation, by hand or by itself.
        self._served: str | None = None
        # The manual stops seen so far, to notice one while no plan is active.
        self._epoch = jobs.manual_stop_epoch
        self.book = WeeklyBook()
        self.ready = False
        self.storage_error = False
        self.reason = "service_starting"
        self.revision = secrets.token_hex(16)
        self.consent: SessionConsent | None = None
        self._secret = secrets.token_bytes(32)
        self._last_confirmation: str | None = None
        self._lock = asyncio.Lock()
        self._closing = False

    def _changed(self) -> None:
        self.revision = secrets.token_hex(16)

    def _check(self, revision: object) -> None:
        if not self.ready or self.storage_error or self._closing:
            raise WeeklyError("weekly_storage_unavailable")
        if not isinstance(revision, str) or not hmac.compare_digest(revision, self.revision):
            raise WeeklyError("weekly_changed")

    async def initialize(self) -> None:
        async with self._lock:
            self.consent = None
            try:
                self.book = await asyncio.to_thread(self.store.load)
            except (OSError, ValueError):
                self.storage_error = True
                self.reason = "weekly_storage_unavailable"
            else:
                self.storage_error = False
                self.reason = "inactive"
            self.ready = True
            self._changed()

    def preview_plan(self, raw: object, *, revision: object) -> dict[str, Any]:
        self._check(revision)
        plan = WeeklyPlan.parse(raw)
        self.book.put(plan)
        return {"schema": 1, "revision": self.revision, "plan": plan.snapshot(),
                "upcoming": self._rows(plan), "will_stop_active": self.consent is not None and self.consent.plan_id == plan.identifier}

    async def _save(self, candidate: WeeklyBook) -> None:
        try:
            await finish_goal_save(asyncio.to_thread(self.store.save, candidate))
        except (OSError, ValueError) as err:
            self.storage_error = True
            self.reason = "weekly_storage_unavailable"
            self._changed()
            raise WeeklyError("weekly_storage_unavailable") from err
        except asyncio.CancelledError:
            # The worker finished, but an interrupted request must not retain
            # stale RAM as authority for a retry. Recovery requires reload.
            self.storage_error = True
            self.reason = "weekly_storage_unavailable"
            self._changed()
            raise
        self.book = candidate
        self._changed()

    async def save_plan(self, raw: object, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check(revision)
            plan = WeeklyPlan.parse(raw)
            candidate = self.book.put(plan)
            if self.consent is not None and self.consent.plan_id == plan.identifier:
                await self._disarm("plan_changed")
            await self._save(candidate)
            return self.snapshot()

    async def remove_plan(self, identifier: object, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check(revision)
            candidate = self.book.remove(identifier)
            if self.consent is not None and self.consent.plan_id == identifier:
                await self._disarm("plan_changed")
            await self._save(candidate)
            return self.snapshot()

    def _next(self, plan: WeeklyPlan, now: datetime, until: datetime) -> WeeklyOccurrence | None:
        return next((row for row in plan.upcoming(now=now, horizon_days=7, limit=32)
                     if row.status == "ready" and row.finish_at is not None
                     and row.finish_at <= until and not self.book.used(plan.identifier, row.local_date)), None)

    def _signature(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._secret, raw, hashlib.sha256).hexdigest()

    def preview_activation(
        self, plan_id: object, profile: object, *, revision: object, scope: object,
    ) -> dict[str, Any]:
        self._check(revision)
        if self.consent is not None:
            raise WeeklyError("weekly_already_active")
        plan = self.book.plan(plan_id)
        sample = self.jobs.inputs()
        if not isinstance(profile, str) or profile != plan.vehicle_profile or scope != sample.scope or sample.scope is None:
            raise WeeklyError("weekly_vehicle_not_confirmed")
        until = sample.now + timedelta(days=7)
        occurrence = self._next(plan, sample.now, until)
        if occurrence is None:
            raise WeeklyError("weekly_no_occurrence")
        proposal = self.jobs.preview(
            occurrence.job_request(now=sample.now, vehicle_profile=profile),
            revision=self.jobs.revision, scope=scope,
        )
        frozen = {"schema": 1, "revision": self.revision, "job_revision": proposal["revision"],
                  "scope": scope, "plan_id": plan.identifier, "vehicle_profile": profile,
                  "issued_at": sample.now.isoformat(), "active_until": until.isoformat(),
                  "occurrence": occurrence.snapshot(), "request": proposal["request"]}
        return {**frozen, "confirmation": self._signature(frozen)}

    async def activate(self, proposal: object) -> dict[str, Any]:
        async with self._lock:
            if not isinstance(proposal, dict) or not isinstance(proposal.get("confirmation"), str):
                raise WeeklyError("weekly_invalid_confirmation")
            signature = proposal["confirmation"]
            if signature == self._last_confirmation:
                # Return the outcome, including a later stop; never extend or
                # recreate the consent after a lost HTTP response.
                return self.snapshot()
            frozen = {key: value for key, value in proposal.items() if key != "confirmation"}
            if not hmac.compare_digest(signature, self._signature(frozen)):
                raise WeeklyError("weekly_invalid_confirmation")
            self._check(frozen.get("revision"))
            sample = self.jobs.inputs()
            issued = job_time(frozen["issued_at"])
            if not 0 <= (sample.now - issued).total_seconds() <= 60:
                raise WeeklyError("weekly_confirmation_expired")
            if self.consent is not None or sample.scope != frozen["scope"]:
                raise WeeklyError("weekly_vehicle_not_confirmed")
            # Recheck the ordinary admission conditions without any write.
            self.jobs.preview(frozen["request"], revision=frozen["job_revision"], scope=frozen["scope"])
            self.consent = SessionConsent(frozen["plan_id"], frozen["vehicle_profile"], frozen["scope"],
                                          job_time(frozen["active_until"]), self.jobs.manual_stop_epoch, sample.now)
            self._served = frozen["scope"]
            self._last_confirmation = signature
            self._changed()
            try:
                await self._dispatch(frozen["request"], frozen["occurrence"]["date"], frozen["job_revision"])
                # Only an activation whose order exists remembers the car.
                vehicle = self._vehicle(frozen["scope"])
                if vehicle is not None:
                    await self._save(self.book.assign(frozen["plan_id"], vehicle))
            except BaseException:
                await self._disarm("activation_unconfirmed")
                raise
            return self.snapshot()

    def _vehicle(self, scope: str | None) -> str | None:
        found = self.confirmed() if self.confirmed is not None else None
        return vehicle_key(found[0]) if found is not None and scope is not None and found[1] == scope else None

    async def _forget(self, plan_id: str | None) -> None:
        candidate = self.book.unassign(plan_id)
        if candidate != self.book:
            await self._save(candidate)

    async def _dispatch(self, request: dict[str, Any], day: str, job_revision: str) -> None:
        consent = self.consent
        if consent is None:
            raise WeeklyError("weekly_inactive")
        candidate = self.book.reserve(consent.plan_id, date.fromisoformat(day))
        await self._save(candidate)
        # A stop during the storage await invalidates this permission too.
        if self.jobs.manual_stop_epoch != consent.stop_epoch:
            raise WeeklyError("weekly_manual_stop")
        previous = consent.job_id
        # Track the intended ID before awaiting: cancellation or a lost response
        # can still leave an accepted job, which disarm must be able to stop.
        consent.job_id = request["id"]
        if previous is None:
            await self.jobs.create(request, revision=job_revision, scope=consent.scope)
        else:
            await self.jobs.successor(request, revision=job_revision, scope=consent.scope,
                                      predecessor=previous, stop_epoch=consent.stop_epoch)
        self.reason = "active"
        self._changed()

    async def _disarm(self, reason: str, *, stop: bool = True) -> None:
        consent, self.consent = self.consent, None
        self.reason = reason
        self._changed()
        if stop and consent is not None and self.jobs.job is not None and self.jobs.job.identifier == consent.job_id:
            try:
                await self.jobs.stop()
            finally:
                # The plan's own stop is not one by hand.
                self._epoch = self.jobs.manual_stop_epoch

    async def deactivate(self, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check(revision)
            consent = self.consent
            try:
                # Saved first, so an interrupted stop cannot leave the car
                # remembered. Without an active plan every car is forgotten.
                await self._forget(None if consent is None else consent.plan_id)
            finally:
                await self._disarm("user_stop")
            return self.snapshot()

    def _occurrences(self, plan: WeeklyPlan, now: datetime) -> tuple[WeeklyOccurrence, ...]:
        return plan.upcoming(now=now, horizon_days=7, limit=32)

    async def _release_unstarted(self, plan: WeeklyPlan, now: datetime) -> None:
        """Free a date whose order ended before its window opened.

        An activation by hand creates the order for the next departure at
        once. A car that leaves and returns before that window would
        otherwise have lost the departure it was activated for.
        """
        job = self.jobs.job
        latest = dict(self.book.consumed).get(plan.identifier)
        if job is None or latest is None or job.state != "interrupted" or job.start_at <= now:
            return
        if any(row.identifier == job.identifier and row.local_date == latest for row in self._occurrences(plan, now)):
            await self._save(self.book.release(plan.identifier, latest))

    async def _activate_assigned(self, stopped: bool) -> None:
        """Serve the car a plan was activated for, without a new consent.

        The plan only waits: its order is created when the departure's window
        opens, so Home Assistant controls charging until then and no date is
        used up by plugging in.
        """
        if self.storage_error or self._closing or not self.book.assigned:
            return
        sample = self.jobs.inputs()
        scope, job = sample.scope, self.jobs.job
        if stopped:
            # A stop by hand while no plan was active, as after a restart:
            # the plan whose order it was forgets its car, like an active one.
            for owner, _vehicle in self.book.assigned:
                rows = self._occurrences(self.book.plan(owner), sample.now)
                if job is not None and any(row.identifier == job.identifier for row in rows):
                    self._served = scope
                    with suppress(WeeklyError):
                        await self._forget(owner)
            return
        if not sample.available or sample.present is not True or scope is None or scope == self._served:
            return
        vehicle = self._vehicle(scope)
        plan_id = None if vehicle is None else self.book.assigned_plan(vehicle)
        if plan_id is None:
            return
        plan = self.book.plan(plan_id)
        if not plan.enabled:
            return
        consent = SessionConsent(plan_id, plan.vehicle_profile, scope, sample.now + timedelta(days=7),
                                 self.jobs.manual_stop_epoch, sample.now)
        if job is not None and not job.terminal:
            # After a restart the plan's own order may still run; any other
            # order is waited for.
            if job.binding != scope or all(row.identifier != job.identifier for row in self._occurrences(plan, sample.now)):
                return
            consent.job_id = job.identifier
        else:
            try:
                await self._release_unstarted(plan, sample.now)
            except WeeklyError:
                return
        self.consent, self._served = consent, scope
        self.reason = "active" if consent.job_id is not None else "waiting_next_occurrence"
        self._changed()

    async def tick(self) -> None:
        async with self._lock:
            epoch = self.jobs.manual_stop_epoch
            stopped, self._epoch = epoch != self._epoch, epoch
            consent = self.consent
            if consent is None:
                await self._activate_assigned(stopped)
                return
            sample = self.jobs.inputs()
            if self.storage_error or self._closing:
                await self._disarm("weekly_storage_unavailable")
                return
            stopped = epoch != consent.stop_epoch
            if (
                sample.present is False or stopped
                or sample.now >= consent.expires or sample.now < consent.last_now - timedelta(seconds=5)
            ):
                if sample.now >= consent.expires:
                    # An assigned car that stays plugged in is served on.
                    self._served = None
                try:
                    if stopped:
                        # A stop by hand also ends the automatic activation;
                        # saved first, and a failed save fences the plan.
                        with suppress(WeeklyError):
                            await self._forget(consent.plan_id)
                finally:
                    await self._disarm("session_ended")
                return
            if not sample.available or sample.present is None:
                # A short gap: the order and the connection scope bound it.
                return
            job = self.jobs.job
            own = job is not None and job.identifier == consent.job_id
            if sample.scope != consent.scope:
                if own and job is not None and job.state == "suspended":
                    return
                if not (own and job is not None and not job.terminal and job.binding == sample.scope):
                    await self._disarm("session_ended")
                    return
                # The wallbox proved the same plug-in; the order continues.
                consent.scope = sample.scope
            consent.last_now = sample.now
            # Without an order yet, a plan that activated itself waits for
            # its first window.
            first = consent.job_id is None
            if not first:
                if job is None or not own or job.state in ("stopped", "interrupted"):
                    await self._disarm("job_stopped")
                    return
                if not job.terminal:
                    # The running order stops for these as well; so does the plan.
                    if sample.independent_stop or not sample.permitted:
                        await self._disarm("session_ended")
                    return
                if sample.now < job.finish_at:
                    return
            # Between departures Home Assistant controls charging. The next
            # order starts at its window, once nothing requires a stop and no
            # queued action is pending; it never clears an independent stop.
            plan = self.book.plan(consent.plan_id)
            row = self._next(plan, sample.now, consent.expires)
            if row is None or row.start_at is None or sample.now < row.start_at:
                self.reason = "waiting_next_occurrence"
                return
            if (
                sample.pending or not sample.meter_fresh or not sample.permitted
                or sample.independent_stop
            ):
                return
            request, revision = row.job_request(now=sample.now, vehicle_profile=consent.profile), self.jobs.revision
            if first:
                try:
                    # The ordinary admission, without any write: while another
                    # order holds the wallbox the plan waits and uses no date.
                    proposal = self.jobs.preview(request, revision=revision, scope=consent.scope)
                except ValueError:
                    return
                request, revision = proposal["request"], proposal["revision"]
            try:
                await self._dispatch(request, row.local_date.isoformat(), revision)
            except (OSError, ValueError, DeviceError):
                await self._disarm("activation_unconfirmed")

    async def shutdown(self) -> None:
        async with self._lock:
            self._closing = True
            # The order is not stopped: the job owner suspends it, so it can
            # continue after the restart if the wallbox proves the plug-in.
            await self._disarm("service_restarted", stop=False)

    def _rows(self, plan: WeeklyPlan) -> list[dict[str, Any]]:
        return [{**row.snapshot(), "consumed": self.book.used(plan.identifier, row.local_date)}
                for row in plan.upcoming(now=self.jobs.inputs().now)]

    def waiting(self) -> dict[str, Any] | None:
        """The confirmed plan, its order and the start of its next open window."""
        consent = self.consent
        if consent is None:
            return None
        plan = next((item for item in self.book.plans if item.identifier == consent.plan_id), None)
        starts = [] if plan is None else [
            row.start_at for row in plan.upcoming(now=self.jobs.inputs().now)
            if row.start_at is not None and not self.book.used(plan.identifier, row.local_date)
        ]
        return {"plan_id": consent.plan_id, "job_id": consent.job_id,
                "start_at": starts[0].isoformat() if starts else None}

    def snapshot(self) -> dict[str, Any]:
        sample, consent = self.jobs.inputs(), self.consent
        return {"schema": 1, "revision": self.revision, "ready": self.ready,
                "storage_error": self.storage_error, "reason": self.reason,
                "plans": [{"plan": plan.snapshot(), "upcoming": self._rows(plan),
                           "assigned": any(key == plan.identifier for key, _ in self.book.assigned)}
                          for plan in self.book.plans],
                "active": None if consent is None else {"plan_id": consent.plan_id, "vehicle_profile": consent.profile,
                                                         "until": consent.expires.isoformat(), "job_id": consent.job_id},
                "scope": sample.scope, "available": sample.available, "present": sample.present,
                "max_current_a": sample.current_cap, "job": self.jobs.snapshot()["job"]}
