"""An atomic successor path for an explicitly activated recurring recipe."""

from __future__ import annotations

from typing import Any

from .charge_job import ChargeJob
from .charge_job_controller import ChargeJobController, JobRequestError


class WeeklyChargeJobs(ChargeJobController):
    """Manual stop/clear intent also cancels recurrence after a completed job."""

    manual_stop_epoch = 0
    _stop_unconfirmed = False
    # The order a weekly successor will replace. It keeps its pause instead of
    # handing control back, so the next departure starts from a paused car.
    successor_for: str | None = None

    def _keeps_finished(self) -> bool:
        return self.job is not None and self.job.identifier == self.successor_for

    def stop_requested(self) -> bool:
        """Fence an in-flight start before the stop obtains the owner lock."""
        return self._stop_unconfirmed or super().stop_requested()

    def current_policy(self, owner: str | None, enabling: bool) -> bool | None:
        if enabling and self._stop_unconfirmed:
            return False
        return super().current_policy(owner, enabling)

    def _clear_needs_pause(self) -> bool:
        # A stop still in flight keeps its fence until the pause is confirmed.
        return self._stop_unconfirmed or super()._clear_needs_pause()

    def _candidate(self, request: object, revision: object, scope: object) -> ChargeJob:
        if self._stop_unconfirmed:
            raise JobRequestError("weekly_stop_unconfirmed")
        return super()._candidate(request, revision, scope)

    async def stop(self, *, revision: object = None) -> dict[str, Any]:
        if revision is not None:
            self._check_revision(revision)
        self.manual_stop_epoch += 1
        self._stop_unconfirmed = True
        # Cancellation or a failed save must not withdraw the user's stop.
        # A completed stop leaves the terminal job latch; an explicit clear
        # can also remove this fence, but only after its existing pause checks.
        await super().stop(revision=revision)
        self._stop_unconfirmed = False
        return self.snapshot()

    async def clear(self, *, revision: object) -> dict[str, Any]:
        self._check_revision(revision)
        self.manual_stop_epoch += 1
        await super().clear(revision=revision)
        self._stop_unconfirmed = False
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "manual_stop_pending": self._stop_unconfirmed}

    async def successor(
        self, request: object, *, revision: object, scope: str,
        predecessor: str, stop_epoch: int,
    ) -> dict[str, Any]:
        """Replace only a successfully finished, paused, same-session order.

        There is no clear/create gap in which a manual stop could be lost. The
        weekly owner has already durably consumed the next local date. All new
        enabling writes still traverse the normal job persistence and guards.
        """
        async with self._lock:
            self._check_revision(revision)
            sample, old = self.inputs(), self.job
            if (
                not self.ready or self.storage_error or self._closing or self._stop_unconfirmed
                or self.manual_stop_epoch != stop_epoch
                or old is None or old.identifier != predecessor
                or old.kind != "scheduled" or old.state not in ("reached", "finished")
                or old.scope_ended or old.binding != scope or old.finish_at > sample.now
                or not sample.available or sample.present is not True or sample.scope != scope
                or not sample.meter_fresh or not sample.permitted or sample.independent_stop
                or sample.pending or sample.current_a is None or not 0 < sample.current_a < 6
            ):
                raise JobRequestError("weekly_successor_not_permitted")
            candidate = ChargeJob.create(request, binding=scope, total_kwh=sample.total_kwh, now=sample.now)
            if candidate.kind != "scheduled" or candidate.identifier == old.identifier:
                raise JobRequestError("weekly_successor_not_permitted")
            if candidate.current_a > sample.current_cap:
                raise JobRequestError("job_current_exceeds_cap")
            self.job = candidate
            self._durable = None
            self._settled = False
            self._changed()
            await self._tick_locked()
            if self.storage_error:
                raise JobRequestError("job_storage_unavailable")
            return self.snapshot()
