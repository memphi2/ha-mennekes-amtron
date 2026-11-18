"""Bounded atomic storage for the service's departure plan."""

from __future__ import annotations

from .bound_file import BoundFile

MAX_PLAN_BYTES = 8192


class DepartureStore(BoundFile):
    """The controller validates what it holds."""

    field = "plan"
    what = "departure plan"
    max_bytes = MAX_PLAN_BYTES
    prefix = ".departure-"
