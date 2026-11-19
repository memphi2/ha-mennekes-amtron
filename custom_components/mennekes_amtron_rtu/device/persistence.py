"""Finish a goal save before acting on cancellation."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable
from pathlib import Path


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
            except Exception:  # noqa: BLE001 - read from the task below
                break
        error = None if task.cancelled() else task.exception()
        if error is not None:
            # The caller was cancelled; it must not carry on as if only the
            # save had failed.
            raise asyncio.CancelledError from error
        raise


def replace_file(path: Path, payload: bytes, *, prefix: str) -> None:
    """Replace a private file atomically through a same-directory temporary.

    Raises StorageCommitUncertainError when the new file is visible but the
    directory sync that makes it durable failed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as err:
            raise StorageCommitUncertainError(f"{path.name} replaced but directory sync failed") from err
    finally:
        temporary.unlink(missing_ok=True)
