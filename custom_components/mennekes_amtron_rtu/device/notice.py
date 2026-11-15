"""Report a recurring failure once, and its recovery once, in one log format."""

from __future__ import annotations

import logging


class FailureNotice:
    """``Area: what (reason)``: the first failure warns, repeats stay at debug.

    A failure that recurs on every poll or retry would otherwise fill the log.
    The recovery is reported once, so a reader sees that it ended.
    """

    __slots__ = ("_area", "_failing", "_logger")

    def __init__(self, logger: logging.Logger, area: str) -> None:
        self._logger = logger
        self._area = area
        self._failing = False

    @property
    def failing(self) -> bool:
        return self._failing

    def failed(self, what: str, reason: object = None, *, level: int = logging.WARNING) -> bool:
        """Log a failure; return whether it was the first of this episode."""
        first = not self._failing
        self._failing = True
        text = f" ({reason})" if reason else ""
        self._logger.log(level if first else logging.DEBUG, "%s: %s%s", self._area, what, text)
        return first

    def recovered(self, what: str = "recovered") -> bool:
        """Log the end of a failure episode; return whether there was one."""
        if not self._failing:
            return False
        self._failing = False
        self._logger.info("%s: %s", self._area, what)
        return True
