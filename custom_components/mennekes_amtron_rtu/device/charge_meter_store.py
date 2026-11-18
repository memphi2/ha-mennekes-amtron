"""Bounded atomic storage for the service's charge meter."""

from __future__ import annotations

from .bound_file import BoundFile


class ChargeMeterStore(BoundFile):
    """The charge meter validates what it holds."""

    field = "meter"
    what = "charge meter"
    max_bytes = 524288
    prefix = ".meter-"
