"""Finish a goal save before acting on cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


class StorageCommitUncertainError(OSError):
    """The new file is visible, but its directory sync was not confirmed."""


class StorageStateUnknownError(OSError):
    """A failed save could not be read back to determine the visible state."""


async def finish_goal_save(save: Awaitable[None]) -> None:
    """Shield lock acquisition and storage, including repeated cancellation."""
    task = asyncio.ensure_future(save)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
