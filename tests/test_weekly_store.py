"""Consumed dates survive recipe edits, deletion, failures and service restarts."""

from __future__ import annotations

import json
import stat
from datetime import date

import pytest

from custom_components.mennekes_amtron_rtu.device import weekly_store as m
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
)
from custom_components.mennekes_amtron_rtu.device.weekly_plan import WeeklyPlan

DAY = date(2026, 11, 16)


def recipe(**changes):
    return WeeklyPlan.parse({
        "id": "a" * 32, "vehicle_profile": "fixture", "timezone": "UTC",
        "weekdays": [0], "departure_time": "08:00", "charge_minutes": 120,
        "current_a": 12, "energy_kwh": 10, **changes,
    })


def test_empty_store_has_no_side_effect_and_roundtrip_is_private(tmp_path):
    path = tmp_path / "nested" / "weekly-plans.json"
    store = m.WeeklyStore(path, "fixture")
    book = store.load()
    store.save(book)
    assert not path.exists()
    book = book.put(recipe()).reserve("a" * 32, DAY)
    store.save(book)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert m.WeeklyStore(path, "fixture").load() == book
    before = path.stat().st_mtime_ns
    store.save(book)
    assert path.stat().st_mtime_ns == before
    assert not list(path.parent.glob(".weekly-*"))


def test_removal_and_same_day_edits_never_erase_consumption():
    book = m.WeeklyBook().put(recipe()).reserve("a" * 32, DAY)
    changed = book.put(recipe(current_a=16, vehicle_profile="other"))
    assert changed.used("a" * 32, DAY)
    assert changed.used("a" * 32, date(2026, 1, 1))
    removed = changed.remove("a" * 32)
    assert removed.plans == ()
    assert removed.put(recipe()).used("a" * 32, DAY)
    with pytest.raises(ValueError, match="already consumed"):
        changed.reserve("a" * 32, DAY)
    assert not changed.used("a" * 32, date(2026, 11, 17))
    assert changed.reserve("a" * 32, date(2026, 11, 17)).used("a" * 32, DAY)
    with pytest.raises(ValueError, match="not found"):
        changed.remove("missing")


@pytest.mark.parametrize("raw", [None, [], {}, {"plans": [], "consumed": {}, "active": True}])
def test_book_rejects_unknown_fields_and_resume_authority(raw):
    with pytest.raises(ValueError, match="weekly book fields"):
        m.WeeklyBook.parse(raw)


@pytest.mark.parametrize("plans", [None, {}, [None], [recipe().snapshot()] * 17])
def test_recipe_bounds_and_validation(plans):
    with pytest.raises(ValueError, match=r"weekly plan (count|fields)"):
        m.WeeklyBook.parse({"plans": plans, "consumed": {}})


@pytest.mark.parametrize("other", [recipe(), recipe(id="b" * 32)])
def test_duplicate_plan_or_profile_cannot_compete(other):
    with pytest.raises(ValueError, match="duplicate weekly"):
        m.WeeklyBook.parse({"plans": [recipe().snapshot(), other.snapshot()], "consumed": {}})


@pytest.mark.parametrize("ledger", [None, [], {f"{i:032x}": DAY.isoformat() for i in range(129)}])
def test_ledger_is_bounded_without_silent_pruning(ledger):
    with pytest.raises(ValueError, match="ledger count"):
        m.WeeklyBook.parse({"plans": [], "consumed": ledger})


@pytest.mark.parametrize(("key", "value"), [
    (None, "2026-11-16"), ("bad", "2026-11-16"), ("a" * 32, None),
    ("a" * 32, "2026-2-01"), ("a" * 32, "2026-02-30"), ("a" * 32, "2026-W01-1"),
])
def test_ledger_accepts_only_canonical_local_dates(key, value):
    with pytest.raises(ValueError, match="consumed date"):
        m.WeeklyBook.parse({"plans": [], "consumed": {key: value}})


@pytest.mark.parametrize("raw", [[], {}, {"version": True, "identity": "fixture", "book": {}},
                                 {"version": 1, "identity": "other", "book": {}},
                                 {"version": 2, "identity": "fixture", "book": {}}])
def test_store_checks_format_and_wallbox_identity(tmp_path, raw):
    path = tmp_path / "weekly-plans.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="identity or format"):
        m.WeeklyStore(path, "fixture").load()


@pytest.mark.parametrize("content", [b"{", b"[" * 10000])
def test_invalid_or_recursive_json_is_not_an_empty_book(tmp_path, content):
    path = tmp_path / "weekly-plans.json"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="storage JSON"):
        m.WeeklyStore(path, "fixture").load()


def test_storage_size_is_bounded_in_both_directions(tmp_path, monkeypatch):
    path = tmp_path / "weekly-plans.json"
    path.write_bytes(b" " * (m.MAX_WEEKLY_BYTES + 1))
    store = m.WeeklyStore(path, "fixture")
    with pytest.raises(ValueError, match="size limit"):
        store.load()
    monkeypatch.setattr(store, "_encode", lambda _book: b" " * (m.MAX_WEEKLY_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        store.save(m.WeeklyBook())


@pytest.mark.parametrize("point", ["file_sync", "replace", "directory_open", "directory_sync"])
def test_atomic_failure_never_acknowledges_a_consumption_save(tmp_path, monkeypatch, point):
    path = tmp_path / "weekly-plans.json"
    store = m.WeeklyStore(path, "fixture")
    book = m.WeeklyBook().put(recipe())
    store.save(book)
    original = path.read_bytes()
    real_sync, real_open = m.os.fsync, m.os.open
    calls = 0

    def sync(fd):
        nonlocal calls
        calls += 1
        if (point == "file_sync" and calls == 1) or (point == "directory_sync" and calls == 2):
            raise OSError("simulated sync failure")
        return real_sync(fd)

    def open_directory(name, flags, *args, **kwargs):
        if point == "directory_open" and flags & m.os.O_DIRECTORY:
            raise OSError("simulated directory failure")
        return real_open(name, flags, *args, **kwargs)

    def replace(_self, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(m.os, "fsync", sync)
    monkeypatch.setattr(m.os, "open", open_directory)
    if point == "replace":
        monkeypatch.setattr(m.Path, "replace", replace)
    error = StorageCommitUncertainError if point.startswith("directory") else OSError
    with pytest.raises(error, match=r"simulated|weekly book replaced"):
        store.save(book.reserve("a" * 32, DAY))
    assert not list(tmp_path.glob(".weekly-*"))
    if point.startswith("directory"):
        assert json.loads(path.read_text())["book"]["consumed"]["a" * 32] == DAY.isoformat()
        assert store._last is None
    else:
        assert path.read_bytes() == original
