"""Report a recurring failure once, and its recovery once, in one log format."""

from __future__ import annotations

import logging


class FailureNotice:
    """``Area: what (reason)``: the first failure warns, repeats stay at debug.

    A failure that recurs on every poll or retry would otherwise fill the log.
    A reason that changes is reported again, so the log never names an
    outdated cause; a reason must therefore not carry a counter or a time.
    The recovery is reported once, so a reader sees that it ended.
    """

    __slots__ = ("_area", "_failing", "_logger", "_reason")

    def __init__(self, logger: logging.Logger, area: str) -> None:
        self._logger = logger
        self._area = area
        self._failing = False
        self._reason: str | None = None

    @property
    def failing(self) -> bool:
        return self._failing

    def failed(
        self, what: str, reason: object = None, *,
        level: int = logging.WARNING, exc_info: bool | BaseException = False,
    ) -> bool:
        """Log a failure; return whether it was the first of this episode.

        ``exc_info`` adds the traceback of the exception being handled, or
        of the one given, to the lines that are reported, never to the repeats.
        """
        first = not self._failing
        text = f" ({reason})" if reason else ""
        news = first or text != self._reason
        self._failing = True
        self._reason = text
        self._logger.log(
            level if news else logging.DEBUG, "%s: %s%s", self._area, what, text,
            exc_info=exc_info if news else False,
        )
        return first

    def recovered(self, what: str = "recovered") -> bool:
        """Log the end of a failure episode; return whether there was one."""
        if not self._failing:
            return False
        self._failing = False
        self._logger.info("%s: %s", self._area, what)
        return True
