"""The saved loss statistics: one change at a time, kept only once it is on disk."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from .device.charge_loss import LossStatistics
from .device.notice import FailureNotice
from .device.persistence import StorageCommitUncertainError, StorageStateUnknownError
from .goal_store import _finish_atomic
from .loss_store import LossStore

_LOGGER = logging.getLogger(__name__)
# A disk that refuses is asked again this often, not on every poll.
RETRY_SECONDS = 60


class LossLedger:
    """Statistics as they are on disk, and whether the disk still follows.

    Whoever changes them holds ``lock`` from reading what the change is based
    on until ``commit`` returned: a change is visible only once it is saved.
    """

    def __init__(self, store: LossStore, publish: Callable[[], None]) -> None:
        self.statistics = LossStatistics()
        self.lock = asyncio.Lock()
        # Whether the last save succeeded; estimates are unavailable until it does.
        self.saved = True
        # A file that cannot be read is never written over.
        self.readable = True
        self.closed = False
        self.store = store
        self._publish = publish
        self.attempted_at = float("-inf")
        self._notice = FailureNotice(_LOGGER, "Charging loss")

    async def load(self) -> None:
        try:
            self.statistics = await self.store.async_load()
        except ValueError:
            _LOGGER.warning("Charging loss: saved estimates unusable; starting without them")
        except OSError:
            self.saved = self.readable = False
            _LOGGER.warning("Charging loss: storage cannot be read; optional loss estimates are unavailable")

    @property
    def retry_due(self) -> bool:
        """A failed save is worth another attempt."""
        return not self.saved and self.readable and time.monotonic() - self.attempted_at >= RETRY_SECONDS

    @property
    def resting(self) -> bool:
        """A save just failed: whatever is to be booked waits for the retry."""
        return not self.saved and time.monotonic() - self.attempted_at < RETRY_SECONDS

    async def commit(self, change: Callable[[LossStatistics], object]) -> bool:
        """Apply a change and save it; True once the statistics on disk include it."""
        candidate = LossStatistics.restore(self.statistics.snapshot())
        change(candidate)
        if self.saved and candidate.snapshot() == self.statistics.snapshot():
            return True
        self.attempted_at = time.monotonic()
        # The atomic store completes its write even when its caller is
        # cancelled. Commit the corresponding in-memory snapshot as part of
        # the same protected operation before cancellation reaches the caller.
        await _finish_atomic(asyncio.create_task(self._save(candidate)))
        return self.saved

    async def _save(self, candidate: LossStatistics) -> None:
        try:
            await self.store.async_save(candidate)
        except (StorageCommitUncertainError, StorageStateUnknownError):
            # The replacement is visible or its state could not be read.
            # Retain the candidate for a retry and make estimates unavailable.
            self.statistics = candidate
            self._notice.failed("estimate not saved; retrying without affecting charging")
            self.saved = False
            try:
                self._publish()
            except Exception:  # noqa: BLE001 - preserve the storage failure
                _LOGGER.error("Charging loss: unable to publish an uncertain update")
            return
        except (OSError, ValueError):
            self._notice.failed("estimate not saved; retrying without affecting charging")
            changed = self.saved
            self.saved = False
        else:
            changed = not self.saved or candidate.snapshot() != self.statistics.snapshot()
            self.statistics = candidate
            self.saved = True
            self._notice.recovered("estimate saved again")
        if changed:
            self._publish()
