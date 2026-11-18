"""Bridge short unobserved spans of a plug cycle."""

from __future__ import annotations

UNKNOWN_PRESENCE_GRACE_SECONDS = 120.0
_MAX_POLL_INTERVAL_SECONDS = 300.0


class PresenceGap:
    """Bridge short unobserved spans, for the running cost and the measured charge.

    The span runs from the last known poll, through failed or unknown polls
    or a silent stall. The allowance is two minutes, or two and a half
    observed poll intervals for a slow scan, so one unknown poll is bridged at
    any configured interval but a longer gap is not. The interval is learned
    only from two similar consecutive gaps, so one bridged stall cannot widen
    it, and only raised, so extra refreshes cannot narrow it.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._known_at: float | None = None
        self._interval: float | None = None
        self._gap: float | None = None
        self._unknown = False

    def observe(self, now: float, *, known: bool) -> bool:
        """Record one poll; return true when an unknown span became too long."""
        if self._known_at is not None and now < self._known_at:
            # The clock moved back: measure from this poll, known or not.
            self.reset()
            self._known_at = now
        allowance = max(UNKNOWN_PRESENCE_GRACE_SECONDS, 2.5 * (self._interval or 0.0))
        if known and not self._unknown:
            # Between two known polls only a silent stall longer than any
            # scan interval counts, e.g. a suspended host; a changed scan
            # interval must not end the charge.
            allowance = max(allowance, 2.5 * _MAX_POLL_INTERVAL_SECONDS)
        expired = self._known_at is not None and now - self._known_at > allowance
        if known:
            if not self._unknown and self._known_at is not None:
                gap = now - self._known_at
                if self._gap is not None and abs(gap - self._gap) <= 0.25 * max(gap, self._gap):
                    # Only ever raised: extra refreshes after commands must
                    # not make the regular slow poll look like a stall.
                    self._interval = min(max(gap, self._interval or 0.0), _MAX_POLL_INTERVAL_SECONDS)
                self._gap = gap
            else:
                self._gap = None
            self._known_at, self._unknown = now, False
        else:
            self._unknown = True
        return expired
