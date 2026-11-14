"""Budgets for the synthetic full-history serial/Recorder soak, not a HA quota."""

from __future__ import annotations

import math
from typing import Any

DATABASE_LIMIT = 8 * 1024**2
WAL_LIMIT = 16 * 1024**2
QUEUE_LIMIT = 256
ATTRIBUTE_GROWTH_LIMIT = 2
# Each entity may acquire one unavailable attribute set after its first
# reconnect. These are reused, not another allowance on every reconnect.
# Five changed sensor/binary states per iteration, with room for a status
# transition. Each reconnect can additionally record unavailable + restored
# state once for every enabled entity. Unchanged polls must stay quiet.
ROWS_PER_ITERATION = 8


def recorder_errors(data: dict[str, Any]) -> list[str]:
    """Reject absent recording, excess writes, backlog and incomplete maintenance."""

    summary = data.get("recorder")
    samples = data.get("samples")
    if not isinstance(summary, dict) or not isinstance(samples, list) or not samples:
        return ["Recorder measurements are missing"]

    def integer(values: dict[str, Any], key: str) -> int:
        value = values.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else -1

    errors = []
    entities = integer(summary, "entities")
    purges = integer(summary, "purges")
    if entities <= 0 or purges < 1 or integer(summary, "vacuums") != purges or integer(summary, "deleted_rows") <= 0:
        errors.append("Recorder recording, purge or actual VACUUM was not exercised")
    if summary.get("integrity") != "ok":
        errors.append("Recorder database integrity was not confirmed")
    if not 0 <= integer(summary, "queue_peak") <= QUEUE_LIMIT or integer(summary, "queue_after_drain") != 0:
        errors.append("Recorder queue exceeded its budget or did not drain")
    for key, limit in (("database_peak_bytes", DATABASE_LIMIT), ("wal_peak_bytes", WAL_LIMIT)):
        if not 0 < integer(summary, key) <= limit:
            errors.append(f"Recorder {key} exceeded its budget or was not measured")
    duration = summary.get("maintenance_max_seconds")
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 20:
        errors.append("Recorder maintenance duration is invalid or excessive")
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            errors.append("Recorder sample is invalid")
            continue
        recoveries = integer(sample, "serial_recoveries")
        budget = ROWS_PER_ITERATION * (index + 1) + 2 * entities * recoveries
        if recoveries < 0 or not 0 < integer(sample, "recorder_inserted") <= budget:
            errors.append("Recorder state inserts exceeded the scenario budget or were not measured")
        attribute_budget = ATTRIBUTE_GROWTH_LIMIT + (entities if recoveries > 0 else 0)
        if not 0 <= integer(sample, "recorder_attribute_growth") <= attribute_budget:
            errors.append("Recorder attributes grew unexpectedly")
        if any(integer(sample, key) <= 0 for key in ("recorder_states", "recorded_energy_rows", "recorded_power_rows")):
            errors.append("Recorder lost energy or power history")
        if not 0 < integer(sample, "database_bytes") <= DATABASE_LIMIT or not 0 <= integer(sample, "wal_bytes") <= WAL_LIMIT:
            errors.append("Recorder database or WAL size is invalid or excessive")
    return errors
