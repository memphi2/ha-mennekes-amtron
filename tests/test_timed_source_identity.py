"""A representation-only timestamp change cannot replace an explicit trip choice."""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta, timezone

import pytest

from custom_components.mennekes_amtron_rtu.timed_goal import (
    TimedGoal,
    matches_departure_source,
)
from custom_components.mennekes_amtron_rtu.timed_goal_observer import departure_time
from tests.test_timed_goal import NOW, setup_observer, state

ENTITY = "sensor.departure"
FUTURE = NOW + timedelta(hours=8)
LOCAL = FUTURE.astimezone(timezone(timedelta(hours=1)))


@pytest.mark.parametrize("source", [
    f"entity:{ENTITY}:{FUTURE.isoformat()}",
    f"entity:{ENTITY}:{LOCAL.isoformat()}",
    f"entity:{ENTITY}:2026-11-07T14:00:00Z",
    f"entity:{ENTITY}:2026-11-07T14:00:00.000000+00:00",
])
def test_equivalent_saved_source_offsets_identify_one_trip(source):
    assert matches_departure_source(source, ENTITY, FUTURE)
    assert matches_departure_source(source, ENTITY, LOCAL)


@pytest.mark.parametrize("source", [
    None, "manual", "entity:sensor.other:2026-11-07T14:00:00+00:00",
    f"entity:{ENTITY}:2026-11-07T14:01:00+00:00",
    f"entity:{ENTITY}:2026-11-07T14:00:00",
    f"entity:{ENTITY}:invalid", f"entity:{ENTITY}:0001-01-01T00:00:00+01:00",
])
def test_only_an_aware_instant_from_the_same_entity_matches(source):
    assert not matches_departure_source(source, ENTITY, FUTURE)


def test_naive_candidate_cannot_match_and_source_ingest_canonicalizes_utc():
    source = f"entity:{ENTITY}:{FUTURE.isoformat()}"
    assert not matches_departure_source(source, ENTITY, FUTURE.replace(tzinfo=None))
    parsed = departure_time(state(ENTITY, LOCAL.isoformat()))
    assert parsed == FUTURE
    assert parsed.tzinfo is UTC
    assert departure_time(state(ENTITY, "0001-01-01T00:00:00+01:00")) is None


@pytest.mark.parametrize("selection", ["clear", "manual", "reached"])
@pytest.mark.parametrize("legacy", [False, True])
def test_equivalent_timestamp_preserves_cancel_manual_or_completed_trip_after_restore(monkeypatch, selection, legacy):
    async def run():
        observer, control, hass = setup_observer(monkeypatch)
        observer._entry.options["timed_departure_entity"] = ENTITY
        hass.states[ENTITY] = state(ENTITY, FUTURE.isoformat())
        await observer._evaluate()
        chosen = NOW + timedelta(hours=5)
        if selection == "clear":
            await observer.async_set_deadline(None)
        elif selection == "manual":
            await observer.async_set_deadline(chosen)
        else:
            observer.goal.finish("reached")
        persisted = observer.goal.snapshot()
        if legacy:
            persisted["source"] = f"entity:{ENTITY}:{LOCAL.isoformat()}"
        observer.goal = TimedGoal.restore(persisted)
        before = observer.goal.snapshot()
        observer._store.saved.clear()
        control.commands.clear()
        control.is_paused = True
        # A changed value must not be mistaken for a new departure when only
        # its offset representation changed. Disable writes in the manual
        # case because that deliberately retained active trip may still run.
        if selection == "manual":
            control.read_only = True
        monkeypatch.setattr("custom_components.mennekes_amtron_rtu.timed_goal_observer.time.monotonic", lambda: 301.0)
        hass.states[ENTITY] = state(ENTITY, LOCAL.isoformat())
        await observer._evaluate()
        assert observer.goal.snapshot() == before
        assert observer._store.saved == []
        assert control.commands == []

        # An actually later trip from this same source is still accepted.
        later = FUTURE + timedelta(hours=1)
        hass.states[ENTITY] = state(ENTITY, later.isoformat())
        await observer._evaluate()
        assert observer.goal.deadline == later
        assert observer.goal.finished is None
        assert observer.goal.source == f"entity:{ENTITY}:{later.isoformat()}"
        assert len(observer._store.saved) == 1

    asyncio.run(run())
