"""Session-confirmed weekly recipes feeding the single durable job scheduler."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .charge_job import job_time
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
    """Persist at-most-once occurrence claims, never persist vehicle consent.

    One activation covers only the currently confirmed connection for at most
    seven days. Recipe edits, manual stops, restart, unplugging or a lasting
    connection gap end it. A completed occurrence hands control back to Home
    Assistant; the next one starts at its own window, never after a stopped or
    interrupted occurrence. A failed/uncertain claim is not silently replayed.
    """

    def __init__(self, store: WeeklyStore, jobs: WeeklyChargeJobs) -> None:
        self.store = store
        self.jobs = jobs
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
            self._last_confirmation = signature
            self._changed()
            try:
                await self._dispatch(frozen["request"], frozen["occurrence"]["date"], frozen["job_revision"])
            except BaseException:
                await self._disarm("activation_unconfirmed")
                raise
            return self.snapshot()

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
            await self.jobs.stop()

    async def deactivate(self, *, revision: object) -> dict[str, Any]:
        async with self._lock:
            self._check(revision)
            await self._disarm("user_stop")
            return self.snapshot()

    async def tick(self) -> None:
        async with self._lock:
            consent = self.consent
            if consent is None:
                return
            sample = self.jobs.inputs()
            if self.storage_error or self._closing:
                await self._disarm("weekly_storage_unavailable")
                return
            if (
                sample.present is False or self.jobs.manual_stop_epoch != consent.stop_epoch
                or sample.now >= consent.expires or sample.now < consent.last_now - timedelta(seconds=5)
            ):
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
            try:
                await self._dispatch(row.job_request(now=sample.now, vehicle_profile=consent.profile),
                                     row.local_date.isoformat(), self.jobs.revision)
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

    def snapshot(self) -> dict[str, Any]:
        sample, consent = self.jobs.inputs(), self.consent
        return {"schema": 1, "revision": self.revision, "ready": self.ready,
                "storage_error": self.storage_error, "reason": self.reason,
                "plans": [{"plan": plan.snapshot(), "upcoming": self._rows(plan)} for plan in self.book.plans],
                "active": None if consent is None else {"plan_id": consent.plan_id, "vehicle_profile": consent.profile,
                                                         "until": consent.expires.isoformat(), "job_id": consent.job_id},
                "scope": sample.scope, "available": sample.available, "present": sample.present,
                "max_current_a": sample.current_cap, "job": self.jobs.snapshot()["job"]}
